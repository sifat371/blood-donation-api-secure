"""
Seed script — inserts sample data for manual testing.

Run:  python -m app.db.seed
From: backend/
"""

from datetime import date, datetime, timedelta

from sqlmodel import Session

from app.db.database import engine, create_db_and_tables
from app.db.models import (
    User,
    BloodRequest,
    DonationHistory,
    Notification,
    BloodGroup,
    RequestStatus,
    NotificationType,
)


def seed():
    create_db_and_tables()

    with Session(engine) as session:
        # ── Sample Users ──────────────────────────────────────
        users = [
            User(
                name="Rahim Uddin",
                email="rahim@example.com",
                google_id="google_rahim_001",
                phone="+8801711000001",
                blood_group=BloodGroup.O_POS.value,
                division="Dhaka",
                district="Dhaka",
                upazila="Dhanmondi",
                latitude=23.7461,
                longitude=90.3742,
                is_available=True,
                gender="Male",
                date_of_birth=date(1995, 3, 15),
                last_donation_date=date.today() - timedelta(days=120),
            ),
            User(
                name="Fatima Akter",
                email="fatima@example.com",
                google_id="google_fatima_002",
                phone="+8801711000002",
                blood_group=BloodGroup.A_POS.value,
                division="Dhaka",
                district="Dhaka",
                upazila="Gulshan",
                latitude=23.7925,
                longitude=90.4078,
                is_available=True,
                gender="Female",
                date_of_birth=date(1998, 7, 22),
            ),
            User(
                name="Karim Hossain",
                email="karim@example.com",
                google_id="google_karim_003",
                phone="+8801711000003",
                blood_group=BloodGroup.B_NEG.value,
                division="Chittagong",
                district="Chittagong",
                upazila="Kotwali",
                latitude=22.3569,
                longitude=91.7832,
                is_available=True,
                gender="Male",
                date_of_birth=date(1990, 11, 5),
                last_donation_date=date.today() - timedelta(days=30),
            ),
            User(
                name="Nasreen Begum",
                email="nasreen@example.com",
                google_id="google_nasreen_004",
                phone="+8801711000004",
                blood_group=BloodGroup.AB_POS.value,
                division="Dhaka",
                district="Gazipur",
                upazila="Tongi",
                latitude=23.8783,
                longitude=90.4014,
                is_available=False,
                gender="Female",
                date_of_birth=date(1992, 1, 18),
            ),
            User(
                name="Jahangir Alam",
                email="jahangir@example.com",
                google_id="google_jahangir_005",
                phone="+8801711000005",
                blood_group=BloodGroup.O_NEG.value,
                division="Dhaka",
                district="Dhaka",
                upazila="Mirpur",
                latitude=23.8223,
                longitude=90.3654,
                is_available=True,
                gender="Male",
                date_of_birth=date(1988, 6, 30),
                last_donation_date=date.today() - timedelta(days=95),
            ),
        ]

        for user in users:
            session.add(user)
        session.commit()

        # Refresh to get IDs
        for user in users:
            session.refresh(user)

        # ── Sample Blood Requests ─────────────────────────────
        requests = [
            BloodRequest(
                recipient_id=users[1].id,  # Fatima
                patient_name="Ahmed Ali",
                blood_group=BloodGroup.O_POS.value,
                units=2,
                hospital_name="Dhaka Medical College Hospital",
                hospital_address="Secretariat Rd, Dhaka 1000",
                latitude=23.7265,
                longitude=90.3977,
                needed_date=date.today() + timedelta(days=1),
                contact_number="+8801711000002",
                notes="Urgent — surgery scheduled for tomorrow morning",
                status=RequestStatus.PENDING.value,
            ),
            BloodRequest(
                recipient_id=users[3].id,  # Nasreen
                patient_name="Salma Khatun",
                blood_group=BloodGroup.B_NEG.value,
                units=1,
                hospital_name="Square Hospital",
                hospital_address="18/F, Bir Uttam Qazi Nuruzzaman Sarak, Dhaka",
                latitude=23.7527,
                longitude=90.3816,
                needed_date=date.today() + timedelta(days=3),
                contact_number="+8801711000004",
                status=RequestStatus.PENDING.value,
            ),
        ]

        for req in requests:
            session.add(req)
        session.commit()

        for req in requests:
            session.refresh(req)

        # ── Sample Donation History ───────────────────────────
        donation = DonationHistory(
            donor_id=users[0].id,  # Rahim
            request_id=None,
            date=date.today() - timedelta(days=120),
            recipient="Shahid Clinic Patient",
            hospital="Shahid Suhrawardy Medical College",
            blood_group=BloodGroup.O_POS.value,
            status="Completed",
        )
        session.add(donation)

        # ── Sample Notification ───────────────────────────────
        notification = Notification(
            user_id=users[0].id,  # Rahim
            type=NotificationType.NEW_BLOOD_REQUEST.value,
            title="New blood request nearby",
            body="Ahmed Ali needs 2 units of O+ blood at Dhaka Medical College Hospital.",
            is_read=False,
        )
        session.add(notification)

        session.commit()
        print("[OK] Seed data inserted successfully!")
        print(f"  {len(users)} users")
        print(f"  {len(requests)} blood requests")
        print(f"  1 donation history record")
        print(f"  1 notification")


if __name__ == "__main__":
    seed()
