"""MCP tools for the authenticated chat/agent layer.

All request lifecycle mutations delegate to the same service layer as REST.
Tool arguments are untrusted model output; caller identity always comes from the
authenticated User passed to ``dispatch_tool``.
"""

import logging
from typing import Any, List, Optional

from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.time import utc_now
from app.db.models import (
    BloodRequest,
    DonationHistory,
    Notification,
    RequestStatus,
    User,
    UserLocation,
)
from app.schemas.blood_request import BloodRequestCreate, MIN_REQUEST_UNITS
from app.schemas.mcp import (
    MCP_MAX_RESULTS,
    format_tool_validation_error,
    validate_tool_args,
)
from app.services import request_service
from app.services.auth_service import audit_log
from app.services.eligibility import days_until_eligible, is_eligible
from app.services.geo import haversine_distance

logger = logging.getLogger(__name__)


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
        "description": (
            "Update a blood request lifecycle state: accept one unit, withdraw "
            "your commitment, cancel your request, or complete after all units "
            "have been individually confirmed"
        ),
        "parameters": {
            "request_id": "int (required)",
            "action": "string (required) — accept, withdraw, cancel, or complete",
        },
    },
    {
        "name": "ConfirmDonation",
        "description": (
            "Recipient-only confirmation that one donor commitment actually "
            "completed one unit of the request"
        ),
        "parameters": {
            "request_id": "int (required)",
            "commitment_id": "int (required)",
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
        "description": "Get blood requests with open donor capacity near a location",
        "parameters": {
            "latitude": "float (required)",
            "longitude": "float (required)",
            "radius_km": "float (optional, default 20)",
        },
    },
    {
        "name": "UpdateAvailability",
        "description": "Toggle the current user's donation availability",
        "parameters": {"is_available": "bool (required)"},
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


def _request_state(session: Session, req: BloodRequest, user: User) -> dict:
    """Return the non-sensitive P2 lifecycle summary used by MCP mutations."""
    response = request_service.build_response(session, req, viewer=user)
    return {
        "id": response.id,
        "status": response.status,
        "units": response.units,
        "units_required": response.units_required,
        "units_committed": response.units_committed,
        "units_completed": response.units_completed,
        "remaining_units": response.remaining_units,
        "my_commitment_status": response.my_commitment_status,
    }


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

    results = []
    for donor in session.exec(stmt).all():
        if not is_eligible(donor):
            continue
        dist = haversine_distance(latitude, longitude, donor.latitude, donor.longitude)
        if dist <= radius_km:
            results.append(
                {
                    "id": donor.id,
                    "name": donor.name,
                    "distance_km": round(dist, 2),
                    "blood_group": donor.blood_group,
                    "last_donation_date": (
                        str(donor.last_donation_date)
                        if donor.last_donation_date
                        else None
                    ),
                    "is_available": donor.is_available,
                    "division": donor.division,
                    "district": donor.district,
                    "upazila": donor.upazila,
                }
            )
    results.sort(key=lambda item: item["distance_km"])
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

    try:
        request_service.notify_nearby_donors(session, req)
    except Exception as exc:  # best-effort notification fan-out
        logger.warning("Donor notification failed for request %s: %s", req.id, exc)

    state = _request_state(session, req, user)
    state["message"] = f"Blood request created successfully (ID: {req.id})"
    return state


def update_request(
    session: Session,
    user: User,
    request_id: int,
    action: str,
) -> dict:
    actions = {
        "accept": request_service.accept_request,
        "withdraw": request_service.withdraw_commitment,
        "cancel": request_service.cancel_request,
        "complete": request_service.complete_request,
    }
    normalized = str(action).strip().lower()
    handler = actions.get(normalized)
    if handler is None:
        return {
            "error": (
                f"Unknown action: {action}. Use accept, withdraw, cancel, or complete."
            )
        }

    try:
        req = handler(session, request_id, user)
    except HTTPException as exc:
        return {"error": exc.detail}

    result = _request_state(session, req, user)
    result["message"] = f"Request {request_id} {normalized}ed successfully"
    return result


def confirm_donation(
    session: Session,
    user: User,
    request_id: int,
    commitment_id: int,
) -> dict:
    try:
        req = request_service.confirm_commitment(
            session, request_id, commitment_id, user
        )
    except HTTPException as exc:
        return {"error": exc.detail}

    result = _request_state(session, req, user)
    result["commitment_id"] = commitment_id
    result["message"] = f"Donation commitment {commitment_id} confirmed"
    return result


def get_donation_history(session: Session, user_id: int) -> List[dict]:
    items = session.exec(
        select(DonationHistory)
        .where(DonationHistory.donor_id == user_id)
        .order_by(DonationHistory.date.desc())
        .limit(20)
    ).all()
    return [
        {
            "id": item.id,
            "date": str(item.date),
            "recipient": item.recipient,
            "hospital": item.hospital,
            "blood_group": item.blood_group,
            "status": item.status,
        }
        for item in items
    ]


def check_eligibility(session: Session, user_id: int) -> dict:
    user = session.get(User, user_id)
    if user is None:
        return {"error": "User not found"}
    return {
        "user_id": user.id,
        "name": user.name,
        "is_eligible": is_eligible(user),
        "days_until_eligible": days_until_eligible(user),
        "last_donation_date": (
            str(user.last_donation_date) if user.last_donation_date else None
        ),
        "is_available": user.is_available,
    }


def get_nearby_requests(
    session: Session,
    user: User,
    latitude: float,
    longitude: float,
    radius_km: float = 20,
) -> List[dict]:
    request_service.expire_stale_requests(session)
    requests = session.exec(
        select(BloodRequest).where(
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
    ).all()

    results = []
    for req in requests:
        if request_service.has_completed_commitment(session, req.id, user.id):
            continue
        dist = haversine_distance(latitude, longitude, req.latitude, req.longitude)
        if dist > radius_km:
            continue
        state = _request_state(session, req, user)
        results.append(
            {
                "id": req.id,
                "blood_group": req.blood_group,
                "units": req.units,
                "units_required": state["units_required"],
                "units_committed": state["units_committed"],
                "units_completed": state["units_completed"],
                "remaining_units": state["remaining_units"],
                "my_commitment_status": state["my_commitment_status"],
                "hospital_name": req.hospital_name,
                "needed_date": str(req.needed_date),
                "distance_km": round(dist, 2),
                "status": req.status,
            }
        )
    results.sort(key=lambda item: item["distance_km"])
    return results[:MCP_MAX_RESULTS]


def update_availability(session: Session, user_id: int, is_available: bool) -> dict:
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
        "last_donation_date": (
            str(user.last_donation_date) if user.last_donation_date else None
        ),
        "gender": user.gender,
    }


def update_user_location(
    session: Session, user_id: int, latitude: float, longitude: float
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        return {"error": "User not found"}
    user.latitude = latitude
    user.longitude = longitude
    user.last_location_updated = utc_now()
    user.updated_at = utc_now()
    session.add(user)
    session.add(
        UserLocation(user_id=user_id, latitude=latitude, longitude=longitude)
    )
    session.commit()
    return {
        "user_id": user.id,
        "latitude": latitude,
        "longitude": longitude,
        "message": "Location updated",
    }


def get_notifications(session: Session, user_id: int) -> List[dict]:
    items = session.exec(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
        .order_by(Notification.created_at.desc())
        .limit(20)
    ).all()
    return [
        {
            "id": item.id,
            "type": item.type,
            "title": item.title,
            "body": item.body,
            "created_at": str(item.created_at),
        }
        for item in items
    ]


TOOL_MAP = {
    "FindDonors": find_donors,
    "CreateBloodRequest": create_blood_request,
    "UpdateRequest": update_request,
    "ConfirmDonation": confirm_donation,
    "GetDonationHistory": get_donation_history,
    "CheckEligibility": check_eligibility,
    "GetNearbyRequests": get_nearby_requests,
    "UpdateAvailability": update_availability,
    "GetUserProfile": get_user_profile,
    "UpdateUserLocation": update_user_location,
    "GetNotifications": get_notifications,
}

_TOOLS_TAKING_USER = frozenset(
    {
        "FindDonors",
        "CreateBloodRequest",
        "UpdateRequest",
        "ConfirmDonation",
        "GetNearbyRequests",
    }
)

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

_FORBIDDEN_ARGS = frozenset(
    {"user_id", "recipient_id", "donor_id", "accepted_by"}
)


def dispatch_tool(
    session: Session,
    user: User,
    tool_name: str,
    tool_args: dict,
) -> Any:
    fn = TOOL_MAP.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}
    if not isinstance(tool_args, dict):
        return {"error": "Tool arguments must be an object"}

    safe_args = {key: value for key, value in tool_args.items() if key not in _FORBIDDEN_ARGS}
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
            "error": (
                f"Invalid arguments for {tool_name}: "
                f"{format_tool_validation_error(exc)}"
            )
        }

    try:
        if tool_name in _TOOLS_TAKING_USER:
            return fn(session=session, user=user, **safe_args)
        if tool_name in _TOOLS_TAKING_USER_ID:
            return fn(session=session, user_id=user.id, **safe_args)
        return fn(session=session, **safe_args)
    except HTTPException as exc:
        return {"error": exc.detail}
    except TypeError as exc:
        logger.warning("Bad arguments for %s: %s", tool_name, exc)
        return {"error": f"Invalid arguments for {tool_name}: {exc}"}
