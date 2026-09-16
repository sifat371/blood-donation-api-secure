"""Blood request lifecycle endpoints.

P2 preserves the P1 route names while representing donor participation as
one-unit DonationCommitment rows. The service layer owns authorization,
privacy, status derivation and transactions so REST and MCP cannot drift.
"""

from fastapi import APIRouter, Query, Request, BackgroundTasks
from sqlmodel import select
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.deps import CurrentUser, DbSession
from app.db.models import BloodRequest, RequestStatus
from app.schemas.blood_request import (
    BloodRequestCreate,
    BloodRequestResponse,
    CommitmentResponse,
)
from app.schemas.common import PaginatedResponse
from app.services import request_service
from app.services.geo import haversine_distance
from app.services.auth_service import audit_log

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/blood-requests", tags=["Blood Requests"])


@router.post("", response_model=BloodRequestResponse, status_code=201)
@limiter.limit("10/minute")
def create_request(
    request: Request,
    body: BloodRequestCreate,
    user: CurrentUser,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
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

    background_tasks.add_task(
        request_service.notify_nearby_donors_task, blood_request.id
    )
    return request_service.build_response(session, blood_request, viewer=user)


@router.post("/{request_id}/accept", response_model=BloodRequestResponse)
def accept_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.accept_request(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


@router.post("/{request_id}/withdraw", response_model=BloodRequestResponse)
def withdraw_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.withdraw_commitment(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


@router.post(
    "/{request_id}/commitments/{commitment_id}/confirm",
    response_model=BloodRequestResponse,
)
def confirm_commitment(
    request_id: int,
    commitment_id: int,
    user: CurrentUser,
    session: DbSession,
):
    blood_request = request_service.confirm_commitment(
        session, request_id, commitment_id, user
    )
    return request_service.build_response(session, blood_request, viewer=user)


@router.get(
    "/{request_id}/commitments",
    response_model=list[CommitmentResponse],
)
def list_commitments(request_id: int, user: CurrentUser, session: DbSession):
    return request_service.list_commitments_for_user(session, request_id, user)


@router.post("/{request_id}/complete", response_model=BloodRequestResponse)
def complete_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.complete_request(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


@router.post("/{request_id}/cancel", response_model=BloodRequestResponse)
def cancel_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.cancel_request(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)


# Literal routes must remain above GET /{request_id}.
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
    """Open-capacity requests near a donor."""
    request_service.expire_stale_requests(session)
    stmt = select(BloodRequest).where(
        BloodRequest.status.in_(
            (
                RequestStatus.PENDING.value,
                RequestStatus.PARTIALLY_COMMITTED.value,
            )
        ),
        BloodRequest.latitude.isnot(None),
        BloodRequest.longitude.isnot(None),
        BloodRequest.recipient_id != user.id,
    )
    all_requests = session.exec(stmt).all()

    results = []
    for req in all_requests:
        if request_service.has_completed_commitment(session, req.id, user.id):
            continue
        dist = haversine_distance(latitude, longitude, req.latitude, req.longitude)
        if dist <= radius_km:
            results.append((req, dist))

    results.sort(key=lambda item: item[1])
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
    total = len(
        session.exec(
            select(BloodRequest).where(BloodRequest.recipient_id == user.id)
        ).all()
    )
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


@router.get("/{request_id}", response_model=BloodRequestResponse)
def get_request(request_id: int, user: CurrentUser, session: DbSession):
    blood_request = request_service.get_request_for_user(session, request_id, user)
    return request_service.build_response(session, blood_request, viewer=user)
