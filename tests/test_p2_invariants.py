"""Final P2 invariants that must hold across lifecycle and SQLite races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.time import business_today
from app.db.models import (
    BloodGroup,
    BloodRequest,
    CommitmentStatus,
    DonationCommitment,
    RequestStatus,
    User,
)
from app.services.commitment_service import commit_to_request
from app.services.request_service import expire_stale_requests


def test_overdue_request_with_a_secured_unit_does_not_auto_expire(
    session, sample_user, donor_user
):
    request = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Secured overdue patient",
        blood_group=BloodGroup.O_POS.value,
        units=2,
        hospital_name="Test Hospital",
        needed_date=business_today() + timedelta(days=1),
        contact_number="+8801712345678",
        status=RequestStatus.PENDING.value,
    )
    session.add(request)
    session.commit()
    session.refresh(request)

    commit_to_request(session, request.id, donor_user)
    session.refresh(request)
    assert request.status == RequestStatus.PARTIALLY_COMMITTED.value

    request.needed_date = business_today() - timedelta(days=1)
    session.add(request)
    session.commit()

    assert expire_stale_requests(session) == 0
    session.refresh(request)
    assert request.status == RequestStatus.PARTIALLY_COMMITTED.value

    commitment = session.exec(
        select(DonationCommitment).where(
            DonationCommitment.request_id == request.id,
            DonationCommitment.donor_id == donor_user.id,
        )
    ).one()
    assert commitment.status == CommitmentStatus.COMMITTED.value
    assert commitment.slot_number is not None


def test_file_backed_sqlite_race_never_overbooks_one_unit(tmp_path):
    db_path = tmp_path / "commitment-race.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as setup:
        recipient = User(
            name="Race Recipient",
            email="race-recipient@example.com",
            email_verified=True,
            phone="+8801700010000",
            blood_group=BloodGroup.O_POS.value,
            is_available=True,
            gender="Male",
        )
        donors = [
            User(
                name=f"Race Donor {index}",
                email=f"race-donor-{index}@example.com",
                email_verified=True,
                phone=f"+88018000100{index:02d}",
                blood_group=BloodGroup.O_POS.value,
                is_available=True,
                gender="Male",
            )
            for index in range(3)
        ]
        setup.add(recipient)
        setup.add_all(donors)
        setup.commit()
        setup.refresh(recipient)
        for donor in donors:
            setup.refresh(donor)

        request = BloodRequest(
            recipient_id=recipient.id,
            patient_name="Race Patient",
            blood_group=BloodGroup.O_POS.value,
            units=1,
            hospital_name="Race Hospital",
            needed_date=business_today() + timedelta(days=1),
            contact_number="+8801712345678",
            status=RequestStatus.PENDING.value,
        )
        setup.add(request)
        setup.commit()
        setup.refresh(request)
        request_id = request.id
        donor_ids = [donor.id for donor in donors]

    barrier = Barrier(len(donor_ids))

    def attempt(donor_id: int) -> int:
        with Session(engine) as worker:
            donor = worker.get(User, donor_id)
            barrier.wait()
            try:
                commit_to_request(worker, request_id, donor)
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=len(donor_ids)) as pool:
        statuses = list(pool.map(attempt, donor_ids))

    assert statuses.count(200) == 1
    assert all(code in (200, 409) for code in statuses)

    with Session(engine) as check:
        active = check.exec(
            select(DonationCommitment).where(
                DonationCommitment.request_id == request_id,
                DonationCommitment.status.in_(
                    (
                        CommitmentStatus.COMMITTED.value,
                        CommitmentStatus.COMPLETED.value,
                    )
                ),
            )
        ).all()
        assert len(active) == 1
        assert active[0].slot_number == 1
        stored = check.get(BloodRequest, request_id)
        assert stored.status == RequestStatus.FULLY_COMMITTED.value

    engine.dispose()
