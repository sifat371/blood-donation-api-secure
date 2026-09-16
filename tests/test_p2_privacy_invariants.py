"""Final P2 privacy invariants for commitment visibility."""

from tests.conftest import valid_request_payload


def test_recipient_no_longer_sees_withdrawn_donor_identity_but_donor_sees_own_status(
    recipient_client, donor_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=2)
    ).json()
    request_id = created["id"]

    accepted = donor_client.post(f"/api/v1/blood-requests/{request_id}/accept")
    assert accepted.status_code == 200, accepted.text

    before = recipient_client.get(
        f"/api/v1/blood-requests/{request_id}/commitments"
    )
    assert before.status_code == 200, before.text
    assert len(before.json()) == 1
    assert before.json()[0]["donor_phone"] == "+8801700000001"

    withdrawn = donor_client.post(f"/api/v1/blood-requests/{request_id}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text

    recipient_view = recipient_client.get(
        f"/api/v1/blood-requests/{request_id}/commitments"
    )
    assert recipient_view.status_code == 200, recipient_view.text
    assert recipient_view.json() == []

    donor_view = donor_client.get(
        f"/api/v1/blood-requests/{request_id}/commitments"
    )
    assert donor_view.status_code == 200, donor_view.text
    assert len(donor_view.json()) == 1
    assert donor_view.json()[0]["status"] == "Withdrawn"
    assert donor_view.json()[0]["donor_name"] == "Donor User"
