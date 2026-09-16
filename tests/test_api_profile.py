"""
Profile endpoints — reading, updating, and isolation between accounts.

The rule these tests exist to protect: the profile you read and the profile you
write are the one belonging to the Bearer token, and nothing in the request body
can change that.
"""

from datetime import date, timedelta

import pytest

from app.db.models import BloodGroup, FCMToken, User

API = "/api/v1"


# ── Reading ──────────────────────────────────────────────


def test_get_profile_requires_authentication(client):
    assert client.get(f"{API}/profile/me").status_code == 401


def test_get_profile_returns_the_token_holder(recipient_client, sample_user):
    response = recipient_client.get(f"{API}/profile/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == sample_user.id
    assert body["email"] == sample_user.email
    assert body["blood_group"] == BloodGroup.O_POS.value


def test_two_tokens_read_two_different_profiles(
    recipient_client, donor_client, sample_user, donor_user
):
    """The core multi-user guarantee, at its simplest."""
    mine = recipient_client.get(f"{API}/profile/me").json()
    theirs = donor_client.get(f"{API}/profile/me").json()

    assert mine["id"] == sample_user.id
    assert theirs["id"] == donor_user.id
    assert mine["email"] != theirs["email"]


def test_profile_does_not_expose_the_google_id(recipient_client):
    """An OAuth subject id is an account identifier; it doesn't belong in a payload."""
    body = recipient_client.get(f"{API}/profile/me").json()
    assert "google_id" not in body


# ── Updating ─────────────────────────────────────────────


def test_update_requires_authentication(client):
    response = client.patch(f"{API}/profile/me", json={"name": "Nobody"})
    assert response.status_code == 401


def test_update_persists_a_single_field(recipient_client, session, sample_user):
    response = recipient_client.patch(f"{API}/profile/me", json={"name": "Updated Name"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Updated Name"

    # Persisted, not just echoed — this is what "survives an app restart" means.
    session.expire_all()
    assert session.get(User, sample_user.id).name == "Updated Name"
    assert recipient_client.get(f"{API}/profile/me").json()["name"] == "Updated Name"


def test_a_partial_update_leaves_other_fields_alone(recipient_client, sample_user):
    original_phone = sample_user.phone
    response = recipient_client.patch(
        f"{API}/profile/me", json={"upazila": "Mohammadpur"}
    )
    assert response.status_code == 200
    assert response.json()["upazila"] == "Mohammadpur"
    assert response.json()["phone"] == original_phone
    assert response.json()["blood_group"] == BloodGroup.O_POS.value


def test_an_empty_update_is_rejected(recipient_client):
    """Nothing to do is a client mistake worth naming, not a silent success."""
    assert recipient_client.patch(f"{API}/profile/me", json={}).status_code == 400


def test_updating_coordinates_stamps_the_location_timestamp(
    recipient_client, session, sample_user
):
    before = session.get(User, sample_user.id).last_location_updated

    response = recipient_client.patch(
        f"{API}/profile/me", json={"latitude": 23.8103, "longitude": 90.4125}
    )
    assert response.status_code == 200
    assert response.json()["latitude"] == 23.8103
    assert response.json()["longitude"] == 90.4125

    session.expire_all()
    after = session.get(User, sample_user.id).last_location_updated
    assert after is not None
    assert after != before


def test_coordinates_are_stored_in_the_right_order(recipient_client, session, sample_user):
    """
    Latitude and longitude must not swap on the way through.

    Dhaka is roughly 23.8 N, 90.4 E. A transposed pair would be latitude 90.4 —
    which is off the planet — so this also pins the bounds validation below.
    """
    recipient_client.patch(
        f"{API}/profile/me", json={"latitude": 23.8103, "longitude": 90.4125}
    )
    session.expire_all()
    stored = session.get(User, sample_user.id)
    assert 23 < stored.latitude < 24
    assert 90 < stored.longitude < 91


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": 91.0, "longitude": 90.4},  # latitude past the pole
        {"latitude": -91.0, "longitude": 90.4},
        {"latitude": 23.8, "longitude": 181.0},  # longitude past the date line
        {"latitude": 23.8, "longitude": -181.0},
        {"latitude": 90.4125, "longitude": 23.8103},  # the transposed pair itself
    ],
)
def test_out_of_range_coordinates_are_rejected(recipient_client, payload):
    response = recipient_client.patch(f"{API}/profile/me", json=payload)
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_availability_can_be_toggled_both_ways(recipient_client, session, sample_user):
    off = recipient_client.patch(f"{API}/profile/me", json={"is_available": False})
    assert off.status_code == 200
    assert off.json()["is_available"] is False

    on = recipient_client.patch(f"{API}/profile/me", json={"is_available": True})
    assert on.json()["is_available"] is True

    session.expire_all()
    assert session.get(User, sample_user.id).is_available is True


def test_blood_group_can_be_corrected(recipient_client):
    response = recipient_client.patch(
        f"{API}/profile/me", json={"blood_group": BloodGroup.AB_NEG.value}
    )
    assert response.status_code == 200, response.text
    assert response.json()["blood_group"] == BloodGroup.AB_NEG.value


def test_an_invalid_blood_group_is_rejected(recipient_client):
    response = recipient_client.patch(f"{API}/profile/me", json={"blood_group": "Z+"})
    assert response.status_code == 422, response.text


# ── Isolation on write ───────────────────────────────────


def test_an_update_cannot_target_another_account(
    recipient_client, session, sample_user, donor_user
):
    """
    The classic IDOR attempt: naming someone else in the body.

    `id` isn't an accepted field, so it is ignored rather than honoured; either
    way, the only row that may change is the caller's.
    """
    original_donor_name = donor_user.name

    response = recipient_client.patch(
        f"{API}/profile/me",
        json={"id": donor_user.id, "name": "Hijacked"},
    )
    assert response.status_code in (200, 422), response.text

    session.expire_all()
    assert session.get(User, donor_user.id).name == original_donor_name
    if response.status_code == 200:
        # The caller's own name changed; the target's did not.
        assert session.get(User, sample_user.id).name == "Hijacked"
        assert response.json()["id"] == sample_user.id


def test_email_cannot_be_changed_through_the_profile_endpoint(
    recipient_client, session, sample_user
):
    """Email identifies the account; changing it here would be account takeover."""
    original = sample_user.email
    response = recipient_client.patch(
        f"{API}/profile/me", json={"email": "attacker@example.com", "name": "Still Me"}
    )
    assert response.status_code in (200, 422)

    session.expire_all()
    assert session.get(User, sample_user.id).email == original


def test_the_fcm_token_cannot_be_set_through_the_profile_endpoint(
    recipient_client, session, sample_user
):
    """Push routing has its own endpoint; a profile PATCH must not redirect it."""
    response = recipient_client.patch(
        f"{API}/profile/me", json={"fcm_token": "someone-elses-device", "name": "Me"}
    )
    assert response.status_code in (200, 422)

    session.expire_all()
    assert getattr(session.get(User, sample_user.id), "fcm_token", None) != (
        "someone-elses-device"
    )


def test_two_users_editing_at_once_do_not_overwrite_each_other(
    recipient_client, donor_client, session, sample_user, donor_user
):
    """Interleaved edits from two devices, each landing on its own row."""
    recipient_client.patch(f"{API}/profile/me", json={"name": "Recipient Edited"})
    donor_client.patch(f"{API}/profile/me", json={"name": "Donor Edited"})
    recipient_client.patch(f"{API}/profile/me", json={"upazila": "Uttara"})

    session.expire_all()
    assert session.get(User, sample_user.id).name == "Recipient Edited"
    assert session.get(User, sample_user.id).upazila == "Uttara"
    assert session.get(User, donor_user.id).name == "Donor Edited"
    assert session.get(User, donor_user.id).upazila == "Gulshan"


# ── Profile completion ───────────────────────────────────


def test_complete_profile_fills_in_the_required_fields(recipient_client, session, sample_user):
    response = recipient_client.post(
        f"{API}/profile/complete",
        json={
            "phone": "+8801799999999",
            "blood_group": BloodGroup.B_POS.value,
            "division": "Chattogram",
            "district": "Chattogram",
            "upazila": "Kotwali",
            "gender": "Female",
            "date_of_birth": "1995-04-17",
            "latitude": 22.3569,
            "longitude": 91.7832,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blood_group"] == BloodGroup.B_POS.value
    assert body["division"] == "Chattogram"
    assert body["latitude"] == 22.3569
    # Name isn't part of this payload — it arrives from the verified Google
    # profile at sign-in, so completion can't overwrite it.
    assert body["name"] == sample_user.name

    session.expire_all()
    assert session.get(User, sample_user.id).phone == "+8801799999999"


def test_completing_without_gps_keeps_existing_coordinates(
    recipient_client, session, sample_user
):
    """
    Submitting the form with location permission denied must not blank the map.

    The handler uses exclude_none precisely so an absent latitude means "leave it
    as it was", not "set it to null" — a user who loses coordinates disappears
    from donor search.
    """
    original_lat, original_lon = sample_user.latitude, sample_user.longitude

    response = recipient_client.post(
        f"{API}/profile/complete",
        json={
            "phone": "+8801788888888",
            "blood_group": BloodGroup.O_POS.value,
            "division": "Dhaka",
            "district": "Dhaka",
            "upazila": "Dhanmondi",
            "gender": "Male",
            "date_of_birth": "1990-01-01",
        },
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    stored = session.get(User, sample_user.id)
    assert stored.latitude == original_lat
    assert stored.longitude == original_lon


def test_complete_profile_requires_authentication(client):
    response = client.post(f"{API}/profile/complete", json={"name": "Nobody"})
    assert response.status_code == 401


# ── FCM token registration (one row per user, per device) ─


def test_registering_an_fcm_token_binds_it_to_the_caller(
    recipient_client, session, sample_user
):
    response = recipient_client.post(
        f"{API}/profile/fcm-token",
        json={"fcm_token": "device-token-recipient", "device_info": "android"},
    )
    assert response.status_code == 200, response.text

    from sqlmodel import select

    stored = session.exec(
        select(FCMToken).where(FCMToken.token == "device-token-recipient")
    ).first()
    assert stored is not None
    assert stored.user_id == sample_user.id


def test_re_registering_updates_rather_than_duplicates(recipient_client, session, sample_user):
    recipient_client.post(f"{API}/profile/fcm-token", json={"fcm_token": "token-v1"})
    recipient_client.post(f"{API}/profile/fcm-token", json={"fcm_token": "token-v2"})

    from sqlmodel import select

    rows = session.exec(
        select(FCMToken).where(FCMToken.user_id == sample_user.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].token == "token-v2"


def test_a_shared_device_reassigns_the_token_to_whoever_signed_in(
    recipient_client, donor_client, session, sample_user, donor_user
):
    """
    Two accounts, one handset.

    Whoever signed in most recently owns the push token, otherwise the previous
    user keeps receiving alerts meant for the current one.
    """
    shared = "one-physical-device"
    recipient_client.post(f"{API}/profile/fcm-token", json={"fcm_token": shared})
    donor_client.post(f"{API}/profile/fcm-token", json={"fcm_token": shared})

    from sqlmodel import select

    rows = session.exec(select(FCMToken).where(FCMToken.token == shared)).all()
    assert len(rows) == 1
    assert rows[0].user_id == donor_user.id


def test_fcm_registration_requires_authentication(client):
    response = client.post(f"{API}/profile/fcm-token", json={"fcm_token": "anon"})
    assert response.status_code == 401


# ── Donation history ─────────────────────────────────────


def test_donation_history_is_empty_for_a_new_user(recipient_client):
    response = recipient_client.get(f"{API}/profile/donation-history")
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


def test_donation_history_shows_only_your_own_donations(
    recipient_client, donor_client, session, sample_user, donor_user
):
    from app.db.models import DonationHistory

    session.add(
        DonationHistory(
            donor_id=donor_user.id,
            blood_group=donor_user.blood_group,
            date=date.today() - timedelta(days=5),
            status="Completed",
            hospital_name="Square Hospital",
        )
    )
    session.commit()

    assert donor_client.get(f"{API}/profile/donation-history").json()["total"] == 1
    assert recipient_client.get(f"{API}/profile/donation-history").json()["total"] == 0


def test_donation_history_requires_authentication(client):
    assert client.get(f"{API}/profile/donation-history").status_code == 401
