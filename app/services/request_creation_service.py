"""Transactional blood-request creation shared by REST and MCP."""

from sqlmodel import Session

from app.db.models import BloodRequest, RequestStatus, User
from app.schemas.blood_request import BloodRequestCreate
from app.services.auth_service import audit_log
from app.services.outbox_service import enqueue_outbox_event


def create_blood_request(
    session: Session,
    user: User,
    validated: BloodRequestCreate,
) -> BloodRequest:
    """Create request, audit and durable fan-out event atomically."""
    blood_request = BloodRequest(
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
        enqueue_outbox_event(
            session,
            "blood_request_created",
            "blood_request",
            str(blood_request.id),
            {"request_id": blood_request.id},
            f"blood_request_created:{blood_request.id}",
        )
        session.commit()
        session.refresh(blood_request)
        return blood_request
    except Exception:
        session.rollback()
        raise
