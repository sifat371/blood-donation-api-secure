"""
Tests for auth services.
"""

from app.services.auth_service import (
    create_access_token,
    decode_access_token,
    create_refresh_token,
    rotate_refresh_token,
    revoke_refresh_token,
)


def test_create_and_decode_access_token():
    token = create_access_token(42)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_decode_invalid_token():
    payload = decode_access_token("invalid.token.here")
    assert payload is None


def test_refresh_token_rotation(session, sample_user):
    raw = create_refresh_token(session, sample_user.id)
    assert raw is not None
    assert len(raw) > 20

    new_access, new_refresh, uid = rotate_refresh_token(session, raw)
    assert new_access is not None
    assert new_refresh is not None
    assert uid == sample_user.id

    # Old token should be revoked — using it again triggers reuse detection
    bad_access, bad_refresh, bad_uid = rotate_refresh_token(session, raw)
    assert bad_access is None
    assert bad_refresh is None


def test_revoke_refresh_token(session, sample_user):
    raw = create_refresh_token(session, sample_user.id)
    assert revoke_refresh_token(session, raw) is True
    assert revoke_refresh_token(session, "nonexistent") is False
