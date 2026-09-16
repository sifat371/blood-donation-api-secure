"""
Donor search — the query that decides whether a request finds anybody.

Covers the three filters that combine to make a donor discoverable (blood group,
availability, 90-day eligibility), the distance maths, and the pagination the app
relies on. A donor who is silently excluded is indistinguishable from no donors
existing, so each exclusion is asserted on its own.
"""

from datetime import date, timedelta

import pytest

from app.db.models import BloodGroup, User

API = "/api/v1"

# Dhanmondi — where sample_user is, and the origin for most searches below.
ORIGIN = {"latitude": 23.7461, "longitude": 90.3742}


def _search(client, **overrides):
    params = {"blood_group": BloodGroup.O_POS.value, "radius_km": 50, **ORIGIN}
    params.update(overrides)
    return client.get(f"{API}/donors/search", params=params)


def _ids(response) -> list[int]:
    return [d["id"] for d in response.json()["items"]]


# ── Authentication and input validation ──────────────────


def test_search_requires_authentication(client):
    assert _search(client).status_code == 401


@pytest.mark.parametrize(
    "bad",
    [
        {"latitude": 91},
        {"latitude": -91},
        {"longitude": 181},
        {"longitude": -181},
        {"radius_km": 0},
        {"radius_km": 500},
        {"limit": 0},
        {"limit": 1000},
        {"offset": -1},
    ],
)
def test_out_of_range_parameters_are_rejected(recipient_client, bad):
    response = _search(recipient_client, **bad)
    assert response.status_code == 422, response.text


def test_missing_required_parameters_are_rejected(recipient_client):
    """blood_group, latitude and longitude have no defaults, on purpose."""
    response = recipient_client.get(f"{API}/donors/search")
    assert response.status_code == 422


def test_no_coordinates_are_assumed_when_none_are_given(recipient_client):
    """
    There is no default location.

    An earlier version of the app fell back to hardcoded Dhaka coordinates,
    which quietly returned Dhaka donors to a user in Sylhet. Requiring the
    caller to supply a position is what prevents that.
    """
    assert recipient_client.get(
        f"{API}/donors/search", params={"blood_group": BloodGroup.O_POS.value}
    ).status_code == 422


# ── The three exclusion rules ────────────────────────────


def test_an_eligible_nearby_donor_is_found(recipient_client, donor_user):
    response = _search(recipient_client)
    assert response.status_code == 200
    assert donor_user.id in _ids(response)


def test_you_are_never_your_own_match(recipient_client, sample_user, donor_user):
    assert sample_user.id not in _ids(_search(recipient_client))


def test_a_different_blood_group_is_excluded(recipient_client, third_user, donor_user):
    """third_user is AB-, so an O+ search must not return them."""
    found = _ids(_search(recipient_client, blood_group=BloodGroup.O_POS.value))
    assert third_user.id not in found
    assert donor_user.id in found

    # And searching for their group finds them instead.
    ab_search = _ids(_search(recipient_client, blood_group=BloodGroup.AB_NEG.value))
    assert third_user.id in ab_search
    assert donor_user.id not in ab_search


def test_an_unavailable_donor_is_excluded(recipient_client, session, donor_user):
    donor_user.is_available = False
    session.add(donor_user)
    session.commit()
    assert donor_user.id not in _ids(_search(recipient_client))


def test_a_recent_donor_is_excluded(recipient_client, ineligible_donor):
    """Donated 10 days ago — inside the 90-day window."""
    assert ineligible_donor.id not in _ids(_search(recipient_client))


def test_a_donor_without_coordinates_is_excluded(recipient_client, session):
    """
    No position means no distance, so they can't be ranked or reached.

    Excluded rather than shown at "0 km", which would send a requester to the
    wrong place.
    """
    nowhere = User(
        name="No Location",
        email="nowhere@example.com",
        blood_group=BloodGroup.O_POS.value,
        is_available=True,
        latitude=None,
        longitude=None,
    )
    session.add(nowhere)
    session.commit()
    session.refresh(nowhere)

    assert nowhere.id not in _ids(_search(recipient_client))


# ── Distance ─────────────────────────────────────────────


def test_the_radius_actually_limits_results(recipient_client, session, donor_user):
    """
    donor_user is ~5.4 km from the origin.

    A 1 km radius must exclude them and a 50 km radius include them — the same
    donor either way, so this isolates the distance filter from every other rule.
    """
    assert donor_user.id in _ids(_search(recipient_client, radius_km=50))
    assert donor_user.id not in _ids(_search(recipient_client, radius_km=1))


def test_a_far_away_donor_is_outside_the_radius(recipient_client, session):
    """Sylhet is ~200 km from Dhaka — well outside any supported radius."""
    sylhet = User(
        name="Sylhet Donor",
        email="sylhet@example.com",
        blood_group=BloodGroup.O_POS.value,
        is_available=True,
        latitude=24.8949,
        longitude=91.8687,
    )
    session.add(sylhet)
    session.commit()
    session.refresh(sylhet)

    assert sylhet.id not in _ids(_search(recipient_client, radius_km=100))
    # Reachable if you widen the search past the actual distance.
    assert sylhet.id in _ids(_search(recipient_client, radius_km=200))


def test_the_reported_distance_is_plausible(recipient_client, donor_user):
    """
    Dhanmondi to Gulshan is ~5–6 km.

    Loose bounds on purpose: this catches a transposed latitude/longitude pair or
    a radians/degrees mix-up, both of which produce answers off by orders of
    magnitude, without pinning the exact Haversine output.
    """
    match = [
        d for d in _search(recipient_client).json()["items"] if d["id"] == donor_user.id
    ][0]
    assert 3 < match["distance_km"] < 9


def test_results_are_sorted_nearest_first(recipient_client, session, ineligible_donor):
    """The app shows this list in order, so the order has to be real."""
    # Three O+ donors at increasing distance from the origin.
    for name, lat, lon in [
            ("Near O+", 23.7500, 90.3800),
            ("Middle O+", 23.7900, 90.4100),
            ("Far O+", 23.8500, 90.4500),
    ]:
        session.add(
            User(
                name=name,
                email=f"{name.replace(' ', '').replace('+', 'pos')}@example.com",
                blood_group=BloodGroup.O_POS.value,
                is_available=True,
                latitude=lat,
                longitude=lon,
            )
        )
    session.commit()

    distances = [d["distance_km"] for d in _search(recipient_client).json()["items"]]
    assert len(distances) >= 3
    assert distances == sorted(distances)


# ── Response shape and pagination ────────────────────────


def test_a_result_carries_what_the_ui_renders(recipient_client, donor_user):
    match = [
        d for d in _search(recipient_client).json()["items"] if d["id"] == donor_user.id
    ][0]
    assert match["name"] == donor_user.name
    assert match["blood_group"] == BloodGroup.O_POS.value
    assert "phone" not in match
    assert match["is_available"] is True
    assert match["distance_km"] is not None


def test_a_result_does_not_leak_the_donors_account_details(recipient_client, donor_user):
    """Public donor discovery must not expose account or direct contact details."""
    match = [
        d for d in _search(recipient_client).json()["items"] if d["id"] == donor_user.id
    ][0]
    assert "email" not in match
    assert "google_id" not in match
    assert "phone" not in match


def test_pagination_splits_the_result_set(recipient_client, session):
    for i in range(5):
        session.add(
            User(
                name=f"Paged Donor {i}",
                email=f"paged{i}@example.com",
                blood_group=BloodGroup.O_POS.value,
                is_available=True,
                latitude=23.75 + i * 0.01,
                longitude=90.38,
            )
        )
    session.commit()

    first = _search(recipient_client, limit=2, offset=0).json()
    second = _search(recipient_client, limit=2, offset=2).json()

    assert first["total"] == second["total"] >= 5
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    # Different pages, no overlap.
    assert {d["id"] for d in first["items"]}.isdisjoint({d["id"] for d in second["items"]})


def test_total_counts_matches_not_just_the_current_page(recipient_client, session):
    for i in range(4):
        session.add(
            User(
                name=f"Counted {i}",
                email=f"counted{i}@example.com",
                blood_group=BloodGroup.O_POS.value,
                is_available=True,
                latitude=23.75,
                longitude=90.38 + i * 0.01,
            )
        )
    session.commit()

    body = _search(recipient_client, limit=1).json()
    assert len(body["items"]) == 1
    assert body["total"] >= 4


def test_no_matches_is_an_empty_list_not_an_error(recipient_client):
    """An empty result is a normal answer the UI must be able to render."""
    response = _search(recipient_client, blood_group=BloodGroup.A_NEG.value)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0
    assert response.json()["has_more"] is False


# ── Two users, two searches ──────────────────────────────


def test_each_caller_searches_from_the_position_they_supply(
    recipient_client, donor_client, sample_user, donor_user
):
    """
    Distance is measured from the coordinates in the request, so the same donor
    is nearer to one caller than the other. This is what makes a per-user search
    per-user rather than per-server.
    """
    # The requester looks for the donor from Dhanmondi.
    from_dhanmondi = [
        d for d in _search(recipient_client).json()["items"] if d["id"] == donor_user.id
    ][0]["distance_km"]

    # The donor looks for the requester from Gulshan — a search starting next
    # door to the donor's own position.
    near_gulshan = _search(
        donor_client, latitude=23.7925, longitude=90.4078, radius_km=1
    )
    assert donor_user.id not in _ids(near_gulshan)  # still never yourself
    assert from_dhanmondi > 1


@pytest.mark.parametrize(
    "days_ago,expected",
    [(0, False), (1, False), (89, False), (90, True), (200, True)],
)
def test_the_ninety_day_boundary(recipient_client, session, donor_user, days_ago, expected):
    donor_user.last_donation_date = date.today() - timedelta(days=days_ago)
    session.add(donor_user)
    session.commit()

    assert (donor_user.id in _ids(_search(recipient_client))) is expected


def test_a_donor_who_never_donated_is_eligible(recipient_client, session, donor_user):
    assert donor_user.last_donation_date is None
    assert donor_user.id in _ids(_search(recipient_client))
