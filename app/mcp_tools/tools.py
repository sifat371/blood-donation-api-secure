"""
MCP Tools (§4) — Tool definitions for the AI agent layer.

Each tool is a thin wrapper over the same service layer used by the REST API.
No parallel logic paths — every tool calls existing services.

Authorisation invariant: every tool acts as, and only as, the authenticated
user passed to `dispatch_tool`. Tool arguments coming from the language model
are untrusted input; any `user_id` in them is discarded.
"""

import logging
from typing import Optional, List, Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from app.db.models import (
    User,
    BloodRequest,
    DonationHistory,
    Notification,
    UserLocation,
    RequestStatus,
    NotificationType,
)
from app.schemas.blood_request import BloodRequestCreate, MIN_REQUEST_UNITS
from app.schemas.mcp import (
    MCP_MAX_RESULTS,
    format_tool_validation_error,
    validate_tool_args,
)
from app.core.time import utc_now
from app.services import request_service
from app.services.geo import haversine_distance
from app.services.eligibility import is_eligible, days_until_eligible
from app.services.notifications import create_notification
from app.services.auth_service import audit_log

logger = logging.getLogger(__name__)


# ── Tool registry ────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "FindDonors",
        "description": "Find nearest eligible blood donors sorted by distance",
        "parameters": {
            "blood_group": "string (required) — e.g. O+, A-, AB+",
            "latitude": "float (required)",
            "longitude": "float (required)",
            "radius_km": "float (optional, default 20)",
            "division": "string (optional)",
            "district": "string (optional)",
            "upazila": "string (optional)",
        },
    },
    {
        "name": "CreateBloodRequest",
        "description": "Create a new blood request on behalf of the user",
        "parameters": {
            "patient_name": "string (required)",
            "blood_group": "string (required)",
            "units": "int (optional, default 1)",
            "hospital_name": "string (required)",
            "hospital_address": "string (optional)",
            "latitude": "float (optional)",
            "longitude": "float (optional)",
            "needed_date": "string (required, YYYY-MM-DD)",
            "contact_number": "string (required)",
            "notes": "string (optional)",
        },
    },
    {
        "name": "UpdateRequest",
        "description": "Update a blood request status (accept, cancel, or complete)",
        "parameters": {
            "request_id": "int (required)",
            "action": "string (required) — one of: accept, cancel, complete",
        },
    },
    {
        "name": "GetDonationHistory",
        "description": "Get the current user's own donation history",
        "parameters": {},
    },
    {
        "name": "CheckEligibility",
        "description": "Check whether the current user is eligible to donate blood",
        "parameters": {},
    },
    {
        "name": "GetNearbyRequests",
        "description": "Get active blood requests near a location",
        "parameters": {
            "latitude": "float (required)",
            "longitude": "float (required)",
            "radius_km": "float (optional, default 20)",
        },
    },
    {
        "name": "UpdateAvailability",
        "description": "Toggle the current user's donation availability",
        "parameters": {
            "is_available": "bool (required)",
        },
    },
    {
        "name": "GetUserProfile",
        "description": "Get the current user's own profile information",
        "parameters": {},
    },
    {
        "name": "UpdateUserLocation",
        "description": "Update the current user's GPS location",
        "parameters": {
            "latitude": "float (required)",
            "longitude": "float (required)",
        },
    },
    {
        "name": "GetNotifications",
        "description": "Get the current user's pending/unread notifications",
        "parameters": {},
    },
]


# ── Tool implementations ─────────────────────────────────


def find_donors(
    session: Session,
    user: User,
    blood_group: str,
    latitude: float,
    longitude: float,
    radius_km: float = 20,
    division: Optional[str] = None,
    district: Optional[str] = None,
    upazila: Optional[str] = None,
) -> List[dict]:
    """FindDonors tool — mirrors the §2.4 REST search, including exclude-self."""
    stmt = select(User).where(
        User.blood_group == blood_group,
        User.is_available == True,  # noqa: E712
        User.latitude.isnot(None),
        User.longitude.isnot(None),
        User.id != user.id,
    )
    if division:
        stmt = stmt.where(User.division == division)
    if district:
        stmt = stmt.where(User.district == district)
    if upazila:
        stmt = stmt.where(User.upazila == upazila)

    candidates = session.exec(stmt).all()

    results = []
    for donor in candidates:
        if not is_eligible(donor):
            continue
        dist = haversine_distance(latitude, longitude, donor.latitude, donor.longitude)
        if dist <= radius_km:
            results.append({
                "id": donor.id,
                "name": donor.name,
                "distance_km": round(dist, 2),
                "blood_group": donor.blood_group,
                "last_donation_date": str(donor.last_donation_date) if donor.last_donation_date else None,
                "is_available": donor.is_available,
                "division": donor.division,
                "district": donor.district,
                "upazila": donor.upazila,
            })

    results.sort(key=lambda x: x["distance_km"])
    return results[:MCP_MAX_RESULTS]


def create_blood_request(
    session: Session,
    user: User,
    patient_name: str,
    blood_group: str,
    hospital_name: str,
    needed_date: str,
    contact_number: str,
    units: int = MIN_REQUEST_UNITS,
    hospital_address: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    CreateBloodRequest tool.

    Always creates the request for the authenticated user — the agent cannot
    file a request in someone else's name. Validates through the same
    `BloodRequestCreate` schema the REST endpoint uses so the units rule and
    coordinate ranges can't be bypassed via chat, and fans out the same donor
    notifications.
    """
    try:
        validated = BloodRequestCreate(
            patient_name=patient_name,
            blood_group=blood_group,
            units=units,
            hospital_name=hospital_name,
            hospital_address=hospital_address,
            latitude=latitude,
            longitude=longitude,
            needed_date=needed_date,
            contact_number=contact_number,
            notes=notes,
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        return {"error": f"Invalid blood request: {problems}"}

    req = BloodRequest(
        recipient_id=user.id,
        patient_name=validated.patient_name,
        blood_group=validated.blood_group,
        units=validated.units,
        hospital_name=validated.hospital_name,
        hospital_address=validated.hospital_address,
        latitude=validated.latitude,
        longitude=validated.longitude,
        needed_date=validated.needed_date,
        contact_number=validated.contact_number,
        notes=validated.notes,
        status=RequestStatus.PENDING.value,
    )
    try:
        session.add(req)
        session.flush()
        audit_log(
            session,
            user.id,
            "blood_request_created",
            "blood_request",
            str(req.id),
            commit=False,
        )
        session.commit()
        session.refresh(req)
    except Exception:
        session.rollback()
        raise

    # Same donor fan-out as POST /blood-requests, so a request created through
    # chat actually reaches donors.
    try:
        request_service.notify_nearby_donors(session, req)
    except Exception as exc:  # noqa: BLE001 — notification is best-effort
        logger.warning("Donor notification failed for request %s: %s", req.id, exc)

    return {
        "id": req.id,
        "status": req.status,
        "units": req.units,
        "message": f"Blood request created successfully (ID: {req.id})",
    }


def update_request(
    session: Session,
    user: User,
    request_id: int,
    action: str,
) -> dict:
    """
    UpdateRequest tool — delegates to the shared request-lifecycle service, so
    ownership checks, status transitions, donation history, audit logging and
    notifications are identical to the REST endpoints.
    """
    actions = {
        "accept": request_service.accept_request,
        "cancel": request_service.cancel_request,
        "complete": request_service.complete_request,
    }
    handler = actions.get(str(action).strip().lower())
    if handler is None:
        return {
            "error": f"Unknown action: {action}. Use accept, cancel, or complete."
        }

    try:
        req = handler(session, request_id, user)
    except HTTPException as exc:
        return {"error": exc.detail}

    return {
        "id": req.id,
        "status": req.status,
        "message": f"Request {request_id} {action}ed successfully",
    }


def get_donation_history(session: Session, user_id: int) -> List[dict]:
    """GetDonationHistory tool."""
    stmt = (
        select(DonationHistory)
        .where(DonationHistory.donor_id == user_id)
        .order_by(DonationHistory.date.desc())
        .limit(20)
    )
    items = session.exec(stmt).all()
    return [
        {
            "id": d.id,
            "date": str(d.date),
            "recipient": d.recipient,
            "hospital": d.hospital,
            "blood_group": d.blood_group,
            "status": d.status,
        }
        for d in items
    ]


def check_eligibility(session: Session, user_id: int) -> dict:
    """CheckEligibility tool."""
    user = session.get(User, user_id)
    if user is None:
        return {"error": "User not found"}
    eligible = is_eligible(user)
    days_left = days_until_eligible(user)
    return {
        "user_id": user.id,
        "name": user.name,
        "is_eligible": eligible,
        "days_until_eligible": days_left,
        "last_donation_date": str(user.last_donation_date) if user.last_donation_date else None,
        "is_available": user.is_available,
    }


def get_nearby_requests(
    session: Session,
    latitude: float,
    longitude: float,
    radius_km: float = 20,
) -> List[dict]:
    """GetNearbyRequests tool."""
    request_service.expire_stale_requests(session)
    stmt = select(BloodRequest).where(
        BloodRequest.status == RequestStatus.PENDING.value,
        BloodRequest.latitude.isnot(None),
        BloodRequest.longitude.isnot(None),
    )
    all_requests = session.exec(stmt).all()

    results = []
    for req in all_requests:
        dist = haversine_distance(latitude, longitude, req.latitude, req.longitude)
        if dist <= radius_km:
            results.append({
                "id": req.id,
                "blood_group": req.blood_group,
                "units": req.units,
                "hospital_name": req.hospital_name,
                "needed_date": str(req.needed_date),
                "distance_km": round(dist, 2),
                "status": req.status,
            })
    results.sort(key=lambda x: x["distance_km"])
    return results[:MCP_MAX_RESULTS]


def update_availability(session: Session, user_id: int, is_available: bool) -> dict:
    """UpdateAvailability tool."""
    user = session.get(User, user_id)
    if user is None:
        return {"error": "User not found"}
    user.is_available = is_available
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    return {
        "user_id": user.id,
        "is_available": user.is_available,
        "message": f"Availability {'enabled' if is_available else 'disabled'}",
    }


def get_user_profile(session: Session, user_id: int) -> dict:
    """GetUserProfile tool."""
    user = session.get(User, user_id)
    if user is None:
        return {"error": "User not found"}
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "blood_group": user.blood_group,
        "division": user.division,
        "district": user.district,
        "upazila": user.upazila,
        "is_available": user.is_available,
        "last_donation_date": str(user.last_donation_date) if user.last_donation_date else None,
        "gender": user.gender,
    }


def update_user_location(
    session: Session, user_id: int, latitude: float, longitude: float
) -> dict:
    """UpdateUserLocation tool."""
    user = session.get(User, user_id)
    if user is None:
        return {"error": "User not found"}
    user.latitude = latitude
    user.longitude = longitude
    user.last_location_updated = utc_now()
    user.updated_at = utc_now()
    session.add(user)

    # Write breadcrumb to user_locations table
    loc = UserLocation(
        user_id=user_id,
        latitude=latitude,
        longitude=longitude,
    )
    session.add(loc)
    session.commit()

    return {
        "user_id": user.id,
        "latitude": latitude,
        "longitude": longitude,
        "message": "Location updated",
    }


def get_notifications(session: Session, user_id: int) -> List[dict]:
    """GetNotifications tool — returns unread notifications."""
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .limit(20)
    )
    items = session.exec(stmt).all()
    return [
        {
            "id": n.id,
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "created_at": str(n.created_at),
        }
        for n in items
    ]


# ── Dispatcher ───────────────────────────────────────────

TOOL_MAP = {
    "FindDonors": find_donors,
    "CreateBloodRequest": create_blood_request,
    "UpdateRequest": update_request,
    "GetDonationHistory": get_donation_history,
    "CheckEligibility": check_eligibility,
    "GetNearbyRequests": get_nearby_requests,
    "UpdateAvailability": update_availability,
    "GetUserProfile": get_user_profile,
    "UpdateUserLocation": update_user_location,
    "GetNotifications": get_notifications,
}

# Tools that receive the whole authenticated `User` object.
_TOOLS_TAKING_USER = frozenset(
    {"FindDonors", "CreateBloodRequest", "UpdateRequest"}
)

# Tools that read or write data belonging to exactly one user. Their `user_id`
# is always the authenticated user's — never whatever the model supplied.
_TOOLS_TAKING_USER_ID = frozenset(
    {
        "GetDonationHistory",
        "CheckEligibility",
        "UpdateAvailability",
        "GetUserProfile",
        "UpdateUserLocation",
        "GetNotifications",
    }
)

# Argument names no tool is allowed to accept from the model, because they
# would let it act as, or read, another account.
_FORBIDDEN_ARGS = frozenset({"user_id", "recipient_id", "donor_id", "accepted_by"})


def dispatch_tool(
    session: Session,
    user: User,
    tool_name: str,
    tool_args: dict,
) -> Any:
    """
    Dispatch a tool call by name on behalf of `user`.

    `tool_args` originates from the language model and is therefore untrusted:
    identity-bearing arguments are stripped before the call, and the
    authenticated user's id is injected server-side. There is no code path by
    which a chat message can make a tool operate on another account.
    """
    fn = TOOL_MAP.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}

    if not isinstance(tool_args, dict):
        return {"error": "Tool arguments must be an object"}

    safe_args = {k: v for k, v in tool_args.items() if k not in _FORBIDDEN_ARGS}
    dropped = set(tool_args) - set(safe_args)
    if dropped:
        logger.warning(
            "Dropped identity argument(s) %s from %s for user_id=%s",
            sorted(dropped),
            tool_name,
            user.id,
        )

    try:
        safe_args = validate_tool_args(tool_name, safe_args)
    except ValidationError as exc:
        return {
            "error": f"Invalid arguments for {tool_name}: "
            f"{format_tool_validation_error(exc)}"
        }

    try:
        if tool_name in _TOOLS_TAKING_USER:
            return fn(session=session, user=user, **safe_args)
        if tool_name in _TOOLS_TAKING_USER_ID:
            return fn(session=session, user_id=user.id, **safe_args)
        # Location-only lookups — no per-user data involved.
        return fn(session=session, **safe_args)
    except HTTPException as exc:
        return {"error": exc.detail}
    except TypeError as exc:
        # Wrong/missing arguments from the model shouldn't 500 the chat turn.
        logger.warning("Bad arguments for %s: %s", tool_name, exc)
        return {"error": f"Invalid arguments for {tool_name}: {exc}"}
