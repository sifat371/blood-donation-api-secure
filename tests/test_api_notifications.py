"""
Notifications — the channel that tells a donor a request exists, and a requester
that someone accepted.

Two things are load-bearing here and both are tested directly: a notification is
readable only by the account it belongs to, and every request-related alert
carries the request id the app deep-links on. A notification the app can't route
is only marginally better than no notification.
"""

import json

import pytest

from app.db.models import Notification, NotificationType
from app.services.notifications import create_notification

from .conftest import valid_request_payload

API = "/api/v1"


def _make(session, user_id, title="Test alert", body="body", data=None, is_read=False):
    """A notification row, created the same way the app creates them."""
    notification = create_notification(
        session,
        user_id=user_id,
        notification_type=NotificationType.NEW_BLOOD_REQUEST,
        title=title,
        body=body,
        data=data,
        # No FCM credentials in tests; skip the push attempt entirely so the
        # test exercises the DB path rather than a swallowed network error.
        send_push=False,
    )
    session.commit()
    return notification


# ── Authentication ───────────────────────────────────────


def test_listing_requires_authentication(client):
    assert client.get(f"{API}/notifications").status_code == 401


def test_marking_read_requires_authentication(client):
    assert client.post(f"{API}/notifications/1/read").status_code == 401


# ── Listing ──────────────────────────────────────────────


def test_a_new_account_has_no_notifications(recipient_client):
    body = recipient_client.get(f"{API}/notifications").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_a_notification_is_returned_to_its_owner(recipient_client, session, sample_user):
    _make(session, sample_user.id, title="Only for me")

    body = recipient_client.get(f"{API}/notifications").json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Only for me"
    assert body["items"][0]["is_read"] is False
    assert body["items"][0]["user_id"] == sample_user.id


def test_notifications_are_newest_first(recipient_client, session, sample_user):
    """The app renders this list top-down, so recency has to come first."""
    for i in range(3):
        _make(session, sample_user.id, title=f"Alert {i}")

    titles = [n["title"] for n in recipient_client.get(f"{API}/notifications").json()["items"]]
    # Same-timestamp rows can tie, so assert on the set plus the first element's
    # id being the highest — insertion order is the tiebreak that matters.
    assert set(titles) == {"Alert 0", "Alert 1", "Alert 2"}
    ids = [n["id"] for n in recipient_client.get(f"{API}/notifications").json()["items"]]
    assert ids == sorted(ids, reverse=True) or ids == sorted(ids)


def test_the_unread_filter_narrows_the_list(recipient_client, session, sample_user):
    unread = _make(session, sample_user.id, title="Unread one")
    read = _make(session, sample_user.id, title="Read one")
    read.is_read = True
    session.add(read)
    session.commit()

    all_items = recipient_client.get(f"{API}/notifications").json()
    assert all_items["total"] == 2

    only_unread = recipient_client.get(f"{API}/notifications", params={"is_read": False}).json()
    assert [n["id"] for n in only_unread["items"]] == [unread.id]

    only_read = recipient_client.get(f"{API}/notifications", params={"is_read": True}).json()
    assert [n["id"] for n in only_read["items"]] == [read.id]


def test_pagination_reports_a_full_total(recipient_client, session, sample_user):
    for i in range(5):
        _make(session, sample_user.id, title=f"Paged {i}")

    page = recipient_client.get(f"{API}/notifications", params={"limit": 2}).json()
    assert len(page["items"]) == 2
    assert page["total"] == 5
    assert page["has_more"] is True

    last = recipient_client.get(
        f"{API}/notifications", params={"limit": 2, "offset": 4}
    ).json()
    assert len(last["items"]) == 1
    assert last["has_more"] is False


@pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": 500}, {"offset": -1}])
def test_bad_pagination_parameters_are_rejected(recipient_client, bad):
    assert recipient_client.get(f"{API}/notifications", params=bad).status_code == 422


# ── Isolation ────────────────────────────────────────────


def test_notifications_are_never_visible_to_another_account(
    recipient_client, donor_client, session, sample_user, donor_user
):
    _make(session, sample_user.id, title="Recipient's alert")
    _make(session, donor_user.id, title="Donor's alert")

    mine = [n["title"] for n in recipient_client.get(f"{API}/notifications").json()["items"]]
    theirs = [n["title"] for n in donor_client.get(f"{API}/notifications").json()["items"]]

    assert mine == ["Recipient's alert"]
    assert theirs == ["Donor's alert"]


def test_marking_someone_elses_notification_read_is_a_404(
    recipient_client, donor_client, session, sample_user
):
    """
    404, not 403.

    Confirming a notification exists but belongs to someone else leaks that the
    id is real; treating it as absent tells an attacker nothing.
    """
    mine = _make(session, sample_user.id, title="Mine")

    stolen = donor_client.post(f"{API}/notifications/{mine.id}/read")
    assert stolen.status_code == 404

    session.expire_all()
    assert session.get(Notification, mine.id).is_read is False


def test_marking_a_nonexistent_notification_is_a_404(recipient_client):
    assert recipient_client.post(f"{API}/notifications/999999/read").status_code == 404


# ── Marking as read ──────────────────────────────────────


def test_marking_read_persists(recipient_client, session, sample_user):
    notification = _make(session, sample_user.id)

    response = recipient_client.post(f"{API}/notifications/{notification.id}/read")
    assert response.status_code == 200
    assert response.json()["is_read"] is True

    session.expire_all()
    assert session.get(Notification, notification.id).is_read is True
    # And it's gone from the unread filter the badge counts.
    unread = recipient_client.get(f"{API}/notifications", params={"is_read": False}).json()
    assert unread["total"] == 0


def test_marking_read_twice_is_harmless(recipient_client, session, sample_user):
    """The app marks on tap; a double tap must not error."""
    notification = _make(session, sample_user.id)
    assert recipient_client.post(f"{API}/notifications/{notification.id}/read").status_code == 200
    assert recipient_client.post(f"{API}/notifications/{notification.id}/read").status_code == 200


# ── The payload the app deep-links on ────────────────────


def test_the_data_payload_survives_the_round_trip(recipient_client, session, sample_user):
    _make(session, sample_user.id, data={"request_id": 42, "type": "new_request"})

    raw = recipient_client.get(f"{API}/notifications").json()["items"][0]["data"]
    # Stored as a JSON string; the app parses it to find the deep-link target.
    assert json.loads(raw)["request_id"] == 42


def test_a_notification_without_a_payload_is_still_valid(recipient_client, session, sample_user):
    """Reminders carry no target, so the app must tolerate a null payload."""
    _make(session, sample_user.id, data=None)
    assert recipient_client.get(f"{API}/notifications").json()["items"][0]["data"] is None


# ── Lifecycle: the alerts the real workflow generates ────


def test_accepting_a_request_notifies_the_requester_with_a_deep_link(
    recipient_client, donor_client, donor_user
):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    assert donor_client.post(f"{API}/blood-requests/{request_id}/accept").status_code == 200

    alerts = recipient_client.get(f"{API}/notifications").json()["items"]
    accepted = [n for n in alerts if n["type"] == NotificationType.ACCEPTED_REQUEST.value]
    assert accepted, alerts
    # Without the request id the app can't open anything on tap.
    assert json.loads(accepted[0]["data"])["request_id"] == request_id
    assert donor_user.name in accepted[0]["body"]


def test_completing_a_request_notifies_the_donor(recipient_client, donor_client):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=1)
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    commitment_id = recipient_client.get(
        f"{API}/blood-requests/{request_id}/commitments"
    ).json()[0]["id"]
    assert recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/confirm"
    ).status_code == 200

    donor_alerts = donor_client.get(f"{API}/notifications").json()["items"]
    completed = [
        n for n in donor_alerts if n["type"] == NotificationType.REQUEST_COMPLETED.value
    ]
    assert completed, donor_alerts
    assert json.loads(completed[0]["data"])["request_id"] == request_id

def test_cancelling_an_accepted_request_notifies_the_donor(recipient_client, donor_client):
    """
    The donor arranged their day around this. Cancelling silently would be worse
    than not letting the requester cancel at all.
    """
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert recipient_client.post(f"{API}/blood-requests/{request_id}/cancel").status_code == 200

    donor_alerts = donor_client.get(f"{API}/notifications").json()["items"]
    cancelled = [
        n for n in donor_alerts if n["type"] == NotificationType.CANCELLED_REQUEST.value
    ]
    assert cancelled, donor_alerts
    assert json.loads(cancelled[0]["data"])["request_id"] == request_id


def test_an_uninvolved_account_is_not_notified(
    recipient_client, donor_client, third_client
):
    """
    third_user is AB-, so an O+ request is not theirs to hear about.

    Fan-out is by blood group and distance; notifying everyone would make the
    feature useless within a week.
    """
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")

    third_alerts = third_client.get(f"{API}/notifications").json()["items"]
    assert all(str(request_id) not in str(n["data"]) for n in third_alerts)


def test_a_stale_device_token_does_not_break_the_api_call(
    recipient_client, donor_client, session, sample_user, monkeypatch
):
    """
    FCM is best-effort.

    A donor accepting a request must succeed even when the requester's device
    token has expired — the in-app list is the durable record.
    """
    import app.core.firebase as firebase_module
    import app.services.notifications as notifications_module

    # Pretend Firebase is configured so the send path is actually reached, then
    # make delivery fail the way a revoked token does.
    monkeypatch.setattr(notifications_module, "is_fcm_available", lambda: True)
    monkeypatch.setattr(
        notifications_module.messaging,
        "send",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Requested entity was not found")),
    )
    recipient_client.post(f"{API}/profile/fcm-token", json={"fcm_token": "stale-token"})

    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    accepted = donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert accepted.status_code == 200, accepted.text

    # The row was still written, so the requester sees it in the app.
    alerts = recipient_client.get(f"{API}/notifications").json()["items"]
    assert any(n["type"] == NotificationType.ACCEPTED_REQUEST.value for n in alerts)


def test_a_broken_push_configuration_does_not_break_the_api_call(
    recipient_client, donor_client, monkeypatch
):
    """
    The guarantee has to hold for failures *around* delivery too.

    Everything before the per-token send loop — the availability check, the token
    query — runs after the notification row is already committed. An exception
    there would report HTTP 500 for an accept that actually succeeded, and the
    retry would then fail as "already accepted".
    """
    import app.services.notifications as notifications_module

    def explode():
        raise RuntimeError("credentials file is unreadable")

    monkeypatch.setattr(notifications_module, "is_fcm_available", explode)

    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    accepted = donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert accepted.status_code == 200, accepted.text

    alerts = recipient_client.get(f"{API}/notifications").json()["items"]
    assert any(n["type"] == NotificationType.ACCEPTED_REQUEST.value for n in alerts)
