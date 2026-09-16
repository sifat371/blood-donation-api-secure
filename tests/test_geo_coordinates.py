"""
Coordinate-order regression tests.

The location-store bug (declared `setLocation(latitude, longitude)`, destructured
`(longitude, latitude)`) silently transposed every coordinate the app stored.
These tests pin the argument order with real, checkable Bangladesh landmarks so a
future transposition fails loudly instead of quietly returning wrong distances.

Reference distances were taken from the coordinates below; they are asserted with
generous tolerances because the point is argument order, not geodetic precision.
"""

import pytest

from app.services.geo import haversine_distance, is_within_radius

# (latitude, longitude) — latitude first, always.
DHAKA = (23.8103, 90.4125)
CHITTAGONG = (22.3569, 91.7832)
RAJSHAHI = (24.3745, 88.6042)
SYLHET = (24.8949, 91.8687)


def test_dhaka_to_chittagong_is_about_215_km():
    d = haversine_distance(*DHAKA, *CHITTAGONG)
    assert 200 < d < 230, d


def test_dhaka_to_rajshahi_is_about_190_km():
    d = haversine_distance(*DHAKA, *RAJSHAHI)
    assert 175 < d < 205, d


def test_dhaka_to_sylhet_is_about_180_km():
    d = haversine_distance(*DHAKA, *SYLHET)
    assert 165 < d < 200, d


def test_identical_points_are_zero_km():
    assert haversine_distance(*DHAKA, *DHAKA) == pytest.approx(0.0, abs=1e-9)


def test_distance_is_symmetric():
    there = haversine_distance(*DHAKA, *CHITTAGONG)
    back = haversine_distance(*CHITTAGONG, *DHAKA)
    assert there == pytest.approx(back)


def test_transposing_the_arguments_changes_the_answer():
    """
    The regression guard. Transposing *both* points partially cancels the error
    (214 km becomes 152 km rather than something absurd), which is part of why
    this hid for so long — the numbers stayed plausible. 60 km of error is still
    far outside any donor-matching radius.
    """
    correct = haversine_distance(*DHAKA, *CHITTAGONG)
    transposed = haversine_distance(
        DHAKA[1], DHAKA[0], CHITTAGONG[1], CHITTAGONG[0]
    )
    assert abs(correct - transposed) > 50, (correct, transposed)


def test_a_transposed_bangladesh_latitude_is_not_a_valid_latitude():
    """
    The invariant that makes transposition detectable at all: Bangladesh's
    latitude band (20.5..26.7) and longitude band (88..92.7) do not overlap, so
    a swapped pair always lands outside the country — and Dhaka's longitude,
    90.41, is not even a legal latitude.
    """
    for lat, lon in (DHAKA, CHITTAGONG, RAJSHAHI, SYLHET):
        assert 20.5 <= lat <= 26.7 and 88.0 <= lon <= 92.7
        # Swapped, the "latitude" is out of the country's latitude band.
        assert not (20.5 <= lon <= 26.7)

    assert DHAKA[1] > 90, "Dhaka's longitude exceeds the maximum legal latitude"


def test_mismatched_repair_state_is_catastrophic_not_subtle():
    """
    Why the stale rows had to be repaired rather than left alone: one corrected
    user and one still-transposed user are half a world apart, so nearby
    matching silently returns nothing.
    """
    fixed = (24.3702733, 88.6370445)
    still_broken = (88.6393217, 24.3736346)
    assert haversine_distance(*fixed, *still_broken) > 5000


def test_two_points_in_the_same_city_are_within_a_short_radius():
    """Dhanmondi → Gulshan, the conftest donor pairing: a few km apart."""
    dhanmondi = (23.7461, 90.3742)
    gulshan = (23.7925, 90.4078)
    d = haversine_distance(*dhanmondi, *gulshan)
    assert 3 < d < 10, d
    assert is_within_radius(*dhanmondi, *gulshan, 20)
    assert not is_within_radius(*dhanmondi, *gulshan, 1)


def test_radius_check_excludes_a_different_city():
    assert not is_within_radius(*DHAKA, *CHITTAGONG, 50)
    assert is_within_radius(*DHAKA, *CHITTAGONG, 300)


def test_repaired_production_coordinates_land_in_bangladesh():
    """
    The values the repair script wrote back for the two real accounts. Latitude
    must be in Bangladesh's latitude band (20.5..26.7), not its longitude band.
    """
    repaired = [(24.3702733, 88.6370445), (24.3736346, 88.6393217)]
    for lat, lon in repaired:
        assert 20.5 <= lat <= 26.7, f"latitude {lat} outside Bangladesh"
        assert 88.0 <= lon <= 92.7, f"longitude {lon} outside Bangladesh"

    # The two accounts are neighbours, so they must be close together.
    assert haversine_distance(*repaired[0], *repaired[1]) < 1.0
