"""PostgreSQL-only transaction and worker-concurrency integration checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.time import business_today, utc_now
from app.db.database import engine
from app.db.models import (
    BloodGroup,
    BloodRequest,
    CommitmentStatus,
    DeliveryStatus,
    DonationCommitment,
    FCMToken,
    Notification,
    NotificationDelivery,
    NotificationType,
    OutboxEvent,
    OutboxStatus,
    RequestStatus,
    User,
)
from app.services.commitment_service import commit_to_request
from app.workers.queue_claims import claim_deliveries, claim_outbox_events


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


def test_postgres_concurrent_outbox_claims_are_disjoint():
    token = uuid4().hex
    now = utc_now()
    with Session(engine) as setup:
        rows = [
            OutboxEvent(
                event_type="pg_claim_test",
                aggregate_type="test",
                aggregate_id=str(index),
                payload_json="{}",
                idempotency_key=f"pg-outbox-{token}-{index}",
                status=OutboxStatus.PENDING.value,
                available_at=now,
            )
            for index in range(6)
        ]
        setup.add_all(rows)
        setup.commit()
        for row in rows:
            setup.refresh(row)
        expected = {row.id for row in rows}

    barrier = Barrier(2)

    def claim(worker_id: str) -> set[int]:
        with Session(engine) as session:
            barrier.wait()
            return set(claim_outbox_events(session, worker_id, 3, now))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(claim, ("pg-worker-a", "pg-worker-b")))

    assert len(first) == 3
    assert len(second) == 3
    assert first.isdisjoint(second)
    assert first | second == expected


def test_postgres_concurrent_delivery_claims_are_disjoint():
    token = uuid4().hex
    now = utc_now()
    with Session(engine) as setup:
        user = User(
            name="PG Queue User",
            email=f"pg-queue-{token}@example.com",
            email_verified=True,
            is_available=True,
        )
        setup.add(user)
        setup.flush()
        device = FCMToken(
            user_id=user.id,
            device_id=f"pg-device-{token}",
            token=f"pg-token-{token}",
            device_info="android",
        )
        setup.add(device)
        setup.flush()
        deliveries = []
        for index in range(6):
            notification = Notification(
                user_id=user.id,
                type=NotificationType.PROFILE_REMINDER.value,
                title=f"PG delivery {index}",
                body="body",
            )
            setup.add(notification)
            setup.flush()
            delivery = NotificationDelivery(
                notification_id=notification.id,
                fcm_token_id=device.id,
                status=DeliveryStatus.PENDING.value,
                available_at=now,
            )
            setup.add(delivery)
            deliveries.append(delivery)
        setup.commit()
        for row in deliveries:
            setup.refresh(row)
        expected = {row.id for row in deliveries}

    barrier = Barrier(2)

    def claim(worker_id: str) -> set[int]:
        with Session(engine) as session:
            barrier.wait()
            return set(claim_deliveries(session, worker_id, 3, now))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(claim, ("pg-delivery-a", "pg-delivery-b")))

    assert len(first) == 3
    assert len(second) == 3
    assert first.isdisjoint(second)
    assert first | second == expected


def test_postgres_stale_processing_rows_are_reclaimable():
    token = uuid4().hex
    now = utc_now()
    with Session(engine) as setup:
        row = OutboxEvent(
            event_type="pg_stale_test",
            aggregate_type="test",
            aggregate_id="1",
            payload_json="{}",
            idempotency_key=f"pg-stale-{token}",
            status=OutboxStatus.PROCESSING.value,
            available_at=now,
            locked_at=now - timedelta(minutes=5),
            locked_by="dead-worker",
        )
        setup.add(row)
        setup.commit()
        setup.refresh(row)
        event_id = row.id

    with Session(engine) as worker:
        claimed = claim_outbox_events(
            worker,
            "replacement-worker",
            10,
            now,
            lease_seconds=120,
        )

    assert event_id in claimed
    with Session(engine) as check:
        stored = check.get(OutboxEvent, event_id)
        assert stored.status == OutboxStatus.PROCESSING.value
        assert stored.locked_by == "replacement-worker"
        assert stored.locked_at == now
