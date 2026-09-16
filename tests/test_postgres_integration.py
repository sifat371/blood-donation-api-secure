"""PostgreSQL-only P2 transaction/concurrency integration checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.time import business_today
from app.db.database import engine
from app.db.models import (
    BloodGroup,
    BloodRequest,
    CommitmentStatus,
    DonationCommitment,
    RequestStatus,
    User,
)
from app.services.commitment_service import commit_to_request


pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="requires the PostgreSQL CI service",
)


def test_postgres_concurrent_commitments_lock_capacity():
    """N+1 simultaneous donors can never secure more than N PostgreSQL slots."""
    token = uuid4().hex[:10]
    with Session(engine) as setup:
        recipient = User(
            name="Postgres Recipient",
            email=f"pg-recipient-{token}@example.com",
            email_verified=True,
            phone=f"+88017{token[:8]}",
            blood_group=BloodGroup.O_POS.value,
            is_available=True,
            gender="Male",
        )
        donors = [
            User(
                name=f"Postgres Donor {index}",
                email=f"pg-donor-{index}-{token}@example.com",
                email_verified=True,
                phone=f"+88018{index}{token[:7]}",
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

        blood_request = BloodRequest(
            recipient_id=recipient.id,
            patient_name="Postgres Race Patient",
            blood_group=BloodGroup.O_POS.value,
            units=2,
            hospital_name="Postgres Test Hospital",
            needed_date=business_today() + timedelta(days=1),
            contact_number="+8801712345678",
            status=RequestStatus.PENDING.value,
        )
        setup.add(blood_request)
        setup.commit()
        setup.refresh(blood_request)
        request_id = blood_request.id
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

    assert statuses.count(200) == 2
    assert statuses.count(409) == 1

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
        assert len(active) == 2
        assert {item.slot_number for item in active} == {1, 2}
        stored = check.get(BloodRequest, request_id)
        assert stored.status == RequestStatus.FULLY_COMMITTED.value
