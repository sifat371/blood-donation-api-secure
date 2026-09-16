"""
Test fixtures for backend tests.

Provides both:
  * a bare `session` for unit-testing services directly, and
  * a `client` (FastAPI TestClient) plus per-user authenticated clients for
    end-to-end endpoint tests.

Everything runs against a single in-memory SQLite database shared via
StaticPool, so the request handlers, the test body and any background task all
see the same data — and nothing touches the developer's blood_donation.db.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

# Importing app.main registers every model on SQLModel.metadata and builds the
# FastAPI app (routers, middleware) without running the lifespan handler.
import app.db.database as database_module
from app.core.config import settings
from app.core.deps import get_session
from app.db.models import BloodGroup, User
from app.main import app
from app.services.auth_service import create_access_token


@pytest.fixture(name="engine")
def fixture_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Background tasks (e.g. the donor fan-out after creating a request) open
    # their own session from app.db.database.engine — point it at the test DB.
    original_engine = database_module.engine
    database_module.engine = engine
    try:
        yield engine
    finally:
        database_module.engine = original_engine
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(name="session")
def fixture_session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="no_rate_limits", autouse=True)
def fixture_no_rate_limits():
    """
    Disable slowapi so repeated calls in a test don't hit 429.

    Each router module builds its own Limiter, so disable them all.
    """
    from app.api.v1 import auth as auth_api
    from app.api.v1 import blood_requests as requests_api
    from app.main import limiter as app_limiter

    limiters = [app_limiter, auth_api.limiter, requests_api.limiter]
    for lim in limiters:
        lim.enabled = False
    yield
    for lim in limiters:
        lim.enabled = True


@pytest.fixture(name="dev_environment", autouse=True)
def fixture_dev_environment(monkeypatch):
    """Tests must not depend on the developer's local .env values."""
    monkeypatch.setattr(settings, "environment", "development")


@pytest.fixture(name="client")
def fixture_client(engine, session):
    """
    Unauthenticated TestClient.

    Constructed without a `with` block on purpose: entering the context manager
    would run the app lifespan, which creates tables on the *real* database and
    initialises Firebase. Neither belongs in a test run.
    """

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


# ── Users ────────────────────────────────────────────────
#
# sample_user (recipient) and donor_user (donor) are ~5.4 km apart in Dhaka,
# both O+, both available and eligible — the canonical two-user pairing.


@pytest.fixture(name="sample_user")
def fixture_sample_user(session):
    user = User(
        name="Test User",
        email="test@example.com",
        email_verified=True,
        phone="+8801700000000",
        blood_group=BloodGroup.O_POS.value,
        division="Dhaka",
        district="Dhaka",
        upazila="Dhanmondi",
        latitude=23.7461,
        longitude=90.3742,
        is_available=True,
        gender="Male",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="donor_user")
def fixture_donor_user(session):
    user = User(
        name="Donor User",
        email="donor@example.com",
        email_verified=True,
        phone="+8801700000001",
        blood_group=BloodGroup.O_POS.value,
        division="Dhaka",
        district="Dhaka",
        upazila="Gulshan",
        latitude=23.7925,
        longitude=90.4078,
        is_available=True,
        gender="Male",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="third_user")
def fixture_third_user(session):
    """An unrelated account — used to prove data isolation."""
    user = User(
        name="Third Party",
        email="third@example.com",
        email_verified=True,
        phone="+8801700000002",
        blood_group=BloodGroup.AB_NEG.value,
        division="Dhaka",
        district="Dhaka",
        upazila="Mirpur",
        latitude=23.8223,
        longitude=90.3654,
        is_available=True,
        gender="Female",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="ineligible_donor")
def fixture_ineligible_donor(session):
    """Donated 10 days ago — inside the 90-day window, so not eligible."""
    user = User(
        name="Recent Donor",
        email="recent@example.com",
        email_verified=True,
        phone="+8801700000003",
        blood_group=BloodGroup.O_POS.value,
        division="Dhaka",
        district="Dhaka",
        upazila="Banani",
        latitude=23.7940,
        longitude=90.4043,
        is_available=True,
        gender="Male",
        last_donation_date=date.today() - timedelta(days=10),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Tokens and authenticated clients ─────────────────────


@pytest.fixture(name="access_token")
def fixture_access_token(sample_user):
    return create_access_token(sample_user.id)


@pytest.fixture(name="donor_token")
def fixture_donor_token(donor_user):
    return create_access_token(donor_user.id)


def _auth(client: TestClient, token: str) -> TestClient:
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture(name="recipient_client")
def fixture_recipient_client(engine, session, sample_user):
    """TestClient authenticated as sample_user (the requester)."""

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    _auth(test_client, create_access_token(sample_user.id))
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(name="donor_client")
def fixture_donor_client(engine, session, donor_user):
    """TestClient authenticated as donor_user."""

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    _auth(test_client, create_access_token(donor_user.id))
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(name="third_client")
def fixture_third_client(engine, session, third_user):
    """TestClient authenticated as an unrelated third account."""

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    _auth(test_client, create_access_token(third_user.id))
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


# ── Helpers ──────────────────────────────────────────────


def valid_request_payload(**overrides) -> dict:
    """A well-formed POST /blood-requests body, near sample_user."""
    payload = {
        "patient_name": "Karim Rahman",
        "blood_group": BloodGroup.O_POS.value,
        "units": 2,
        "hospital_name": "Dhaka Medical College Hospital",
        "hospital_address": "Bakshibazar, Dhaka",
        "latitude": 23.7261,
        "longitude": 90.3960,
        "needed_date": str(date.today() + timedelta(days=1)),
        "contact_number": "+8801711111111",
        "notes": "Urgent, surgery scheduled",
    }
    payload.update(overrides)
    return payload
