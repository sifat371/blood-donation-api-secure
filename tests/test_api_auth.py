"""
Endpoint tests for authentication.

The refresh flow matters more than it looks: rotation is single-use and
presenting a consumed token trips reuse detection, which revokes *every*
refresh token for that account. A client that refreshes without persisting the
new pair therefore locks itself out permanently on the next restart — the exact
failure these tests guard against.
"""

from datetime import datetime, timedelta

from jose import jwt

from app.core.config import settings
from app.core.time import utc_now
from app.services.auth_service import create_refresh_token


def _dev_login(client, email="newuser@example.com", name="New User"):
    return client.post(
        "/api/v1/auth/dev-login", json={"email": email, "name": name}
    )


# ── Protected endpoints ──────────────────────────────────


def test_protected_endpoint_requires_a_token(client):
    assert client.get("/api/v1/profile/me").status_code == 401


def test_protected_endpoint_rejects_a_malformed_token(client):
    r = client.get(
        "/api/v1/profile/me", headers={"Authorization": "Bearer garbage.token.value"}
    )
    assert r.status_code == 401


def test_protected_endpoint_rejects_an_expired_token(client, sample_user):
    expired = jwt.encode(
        {
            "sub": str(sample_user.id),
            "exp": utc_now() - timedelta(minutes=1),
            "type": "access",
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    r = client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_a_refresh_token_is_not_accepted_as_an_access_token(client, sample_user, session):
    refresh = create_refresh_token(session, sample_user.id)
    r = client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


def test_token_for_a_deleted_user_is_rejected(client, sample_user, session):
    from app.services.auth_service import create_access_token

    token = create_access_token(sample_user.id)
    session.delete(sample_user)
    session.commit()

    r = client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_valid_token_identifies_the_right_user(client, donor_user):
    from app.services.auth_service import create_access_token

    r = client.get(
        "/api/v1/profile/me",
        headers={"Authorization": f"Bearer {create_access_token(donor_user.id)}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "donor@example.com"


# ── Dev login ────────────────────────────────────────────


def test_dev_login_creates_a_user_and_flags_incomplete_profile(client):
    r = _dev_login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["profile_incomplete"] is True


def test_dev_login_is_disabled_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    assert _dev_login(client).status_code == 403


def test_dev_login_returns_a_usable_access_token(client):
    token = _dev_login(client).json()["access_token"]
    r = client.get("/api/v1/profile/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "newuser@example.com"


def test_two_dev_logins_produce_two_distinct_accounts(client):
    a = _dev_login(client, "a@example.com", "A").json()["access_token"]
    b = _dev_login(client, "b@example.com", "B").json()["access_token"]

    id_a = client.get(
        "/api/v1/profile/me", headers={"Authorization": f"Bearer {a}"}
    ).json()["id"]
    id_b = client.get(
        "/api/v1/profile/me", headers={"Authorization": f"Bearer {b}"}
    ).json()["id"]
    assert id_a != id_b


# ── Refresh ──────────────────────────────────────────────


def test_refresh_returns_a_new_token_pair(client):
    tokens = _dev_login(client).json()

    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]


def test_the_rotated_access_token_works(client):
    tokens = _dev_login(client).json()
    rotated = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).json()

    r = client.get(
        "/api/v1/profile/me",
        headers={"Authorization": f"Bearer {rotated['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "newuser@example.com"


def test_the_rotated_refresh_token_can_be_used_again(client):
    """Chained refreshes must keep working — that's what persistence buys."""
    tokens = _dev_login(client).json()

    current = tokens["refresh_token"]
    for _ in range(3):
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": current})
        assert r.status_code == 200, r.text
        current = r.json()["refresh_token"]


def test_reusing_a_consumed_refresh_token_is_rejected(client):
    tokens = _dev_login(client).json()
    old = tokens["refresh_token"]

    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old}).status_code == 200
    # Second use of the same token.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old}).status_code == 401


def test_reuse_detection_revokes_the_whole_token_family(client):
    """
    Why the frontend MUST persist the rotated refresh token: presenting a stale
    one invalidates the fresh one too, so a stale token in SecureStore locks
    the account out rather than merely forcing one re-login.
    """
    tokens = _dev_login(client).json()
    old = tokens["refresh_token"]
    fresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old}
    ).json()["refresh_token"]

    # Replay the consumed token → reuse detected.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old}).status_code == 401
    # ...and the good token is collateral damage.
    assert (
        client.post("/api/v1/auth/refresh", json={"refresh_token": fresh}).status_code
        == 401
    )


def test_refresh_rejects_an_unknown_token(client):
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "nope"})
    assert r.status_code == 401


def test_refresh_rejects_an_expired_token(client, sample_user, session):
    from app.db.models import RefreshToken
    from sqlmodel import select

    raw = create_refresh_token(session, sample_user.id)
    stored = session.exec(select(RefreshToken)).first()
    stored.expires_at = utc_now() - timedelta(days=1)
    session.add(stored)
    session.commit()

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": raw})
    assert r.status_code == 401


# ── Logout ───────────────────────────────────────────────


def test_logout_revokes_the_refresh_token(client):
    tokens = _dev_login(client).json()

    r = client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 204

    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_logout_of_an_unknown_token_is_not_an_error(client):
    """Logout must be idempotent so the client can always clear local state."""
    r = client.post("/api/v1/auth/logout", json={"refresh_token": "already-gone"})
    assert r.status_code == 204


def test_logout_does_not_affect_another_users_session(client):
    a = _dev_login(client, "a@example.com", "A").json()
    b = _dev_login(client, "b@example.com", "B").json()

    client.post("/api/v1/auth/logout", json={"refresh_token": a["refresh_token"]})

    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": b["refresh_token"]}
        ).status_code
        == 200
    )


# ── Google login ─────────────────────────────────────────


def test_google_login_requires_configured_client_id(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    r = client.post("/api/v1/auth/google", json={"id_token": "anything"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "GOOGLE_AUTH_NOT_CONFIGURED"


def test_google_login_rejects_an_invalid_id_token(client, monkeypatch):
    monkeypatch.setattr(
        settings, "google_client_id", "test-client.apps.googleusercontent.com"
    )
    r = client.post("/api/v1/auth/google", json={"id_token": "not-a-google-token"})
    assert r.status_code == 401


def test_refresh_does_not_extend_an_unverified_account(client, sample_user, session):
    from sqlmodel import select
    from app.db.models import RefreshToken

    raw = create_refresh_token(session, sample_user.id)
    sample_user.email_verified = False
    session.add(sample_user)
    session.commit()

    response = client.post("/api/v1/auth/refresh", json={"refresh_token": raw})
    assert response.status_code == 401
    tokens = session.exec(select(RefreshToken).where(RefreshToken.user_id == sample_user.id)).all()
    assert tokens
    assert all(token.revoked for token in tokens)


def test_refresh_does_not_extend_a_deleted_account(client, sample_user, session):
    from sqlmodel import select
    from app.db.models import RefreshToken

    user_id = sample_user.id
    raw = create_refresh_token(session, user_id)
    session.delete(sample_user)
    session.commit()

    response = client.post("/api/v1/auth/refresh", json={"refresh_token": raw})
    assert response.status_code == 401
    tokens = session.exec(select(RefreshToken).where(RefreshToken.user_id == user_id)).all()
    assert tokens
    assert all(token.revoked for token in tokens)
