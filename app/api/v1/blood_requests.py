"""
Blood request lifecycle endpoints (§2.5)

POST /blood-requests               — create
POST /blood-requests/{id}/accept   — donor accepts
POST /blood-requests/{id}/complete — recipient confirms
POST /blood-requests/{id}/cancel   — cancel
GET  /blood-requests/nearby        — active near a location
GET  /blood-requests/mine          — recipient's own requests
GET  /blood-requests/{id}          — single request detail

The authorisation and status-transition rules live in
`app/services/request_service.py` so the AI agent's MCP tools enforce exactly
the same rules.
"""

from fastapi import APIRouter, Query, Request, BackgroundTasks
from sqlmodel import select
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

from app.core.deps import CurrentUser, DbSession
from app.db.models import BloodRequest, RequestStatus
from app.schemas.blood_request import BloodRequestCreate, BloodRequestResponse
from app.schemas.common import PaginatedResponse
from app.services import request_service
from app.services.geo import haversine_distance
from app.services.auth_service import audit_log

router = APIRouter(prefix="/blood-requests", tags=["Blood Requests"])


# ── Create ───────────────────────────────────────────────


@router.post("", response_model=BloodRequestResponse, status_code=201)
@limiter.limit("10/minute")
def create_request(
    request: Request,
    body: BloodRequestCreate,
    user: CurrentUser,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    """Create a new blood request. Notifies nearby eligible donors."""
    blood_request = BloodRequest(
        recipient_id=user.id,
        patient_name=body.patient_name,
        blood_group=body.blood_group,
        units=body.units,
        hospital_name=body.hospital_name,
        hospital_address=body.hospital_address,
        latitude=body.latitude,
        longitude=body.longitude,
        needed_date=body.needed_date,
        contact_number=body.contact_number,
        notes=body.notes,
        status=RequestStatus.PENDING.value,
    )
    try:
        session.add(blood_request)
        session.flush()
        audit_log(
            session,
            user.id,
            "blood_request_created",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        session.commit()
        session.refresh(blood_request)
    except Exception:
        session.rollback()
        raise

    # Notify nearby eligible donors after the response is sent.
    background_tasks.add_task(
        request_service.notify_nearby_donors_task, blood_request.id
    )

    return request_service.build_response(session, blood_request, viewer=user)


# ── Accept ───────────────────────────────────────────────


@router.post("/{request_id}/accept", response_model=BloodRequestResponse)
def accept_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.accept_request(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


# ── Complete ─────────────────────────────────────────────


@router.post("/{request_id}/complete", response_model=BloodRequestResponse)
def complete_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.complete_request(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


# ── Cancel ───────────────────────────────────────────────


@router.post("/{request_id}/cancel", response_model=BloodRequestResponse)
def cancel_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.cancel_request(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


# ── Nearby ───────────────────────────────────────────────
#
# NOTE: the literal routes /nearby and /mine must stay declared *above*
# GET /{request_id}, otherwise the path-parameter route would shadow them.


@router.get("/nearby", response_model=PaginatedResponse[BloodRequestResponse])
def nearby_requests(
    user: CurrentUser,
    session: DbSession,
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(20, ge=1, le=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Active (Pending) requests near a location, for donors to browse."""
    request_service.expire_stale_requests(session)
    stmt = select(BloodRequest).where(
        BloodRequest.status == RequestStatus.PENDING.value,
        BloodRequest.latitude.isnot(None),
        BloodRequest.longitude.isnot(None),
        # Your own requests belong in /mine, not in the donor feed.
        BloodRequest.recipient_id != user.id,
    )
    all_requests = session.exec(stmt).all()

    results = []
    for req in all_requests:
        dist = haversine_distance(latitude, longitude, req.latitude, req.longitude)
        if dist <= radius_km:
            results.append((req, dist))

    results.sort(key=lambda x: x[1])
    total = len(results)
    page = results[offset : offset + limit]

    items = [
        request_service.build_response(session, req, viewer=user, distance_km=dist)
        for req, dist in page
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


# ── My requests ──────────────────────────────────────────


@router.get("/mine", response_model=PaginatedResponse[BloodRequestResponse])
def my_requests(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    request_service.expire_stale_requests(session)
    stmt = (
        select(BloodRequest)
        .where(BloodRequest.recipient_id == user.id)
        .order_by(BloodRequest.created_at.desc())
    )
    count_stmt = select(BloodRequest).where(BloodRequest.recipient_id == user.id)
    total = len(session.exec(count_stmt).all())

    items_raw = session.exec(stmt.offset(offset).limit(limit)).all()
    items = [
        request_service.build_response(session, req, viewer=user)
        for req in items_raw
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


# ── Single request detail ────────────────────────────────


@router.get("/{request_id}", response_model=BloodRequestResponse)
def get_request(request_id: int, user: CurrentUser, session: DbSession):
    """
    Detail view for one request.

    Visible to the recipient and the accepting donor at any status; visible to
    any other authenticated user only while the request is Pending or Accepted
    (donors reach it from the nearby feed or a push notification). Anything
    else — including a request that doesn't exist — returns 404 so IDs can't be
    enumerated. The donor's name and phone are only ever returned to the
    recipient and to the donor themselves.
    """
    blood_request = request_service.get_request_for_user(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)
