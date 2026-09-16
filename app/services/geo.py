"""
Haversine distance calculator and geo-related utilities.
"""

import math
from typing import Optional


EARTH_RADIUS_KM = 6371.0


def haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """
    Calculate the great-circle distance between two points
    on Earth using the Haversine formula.

    Returns distance in kilometres.
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def is_within_radius(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    radius_km: float,
) -> bool:
    """Check whether two points are within *radius_km* of each other."""
    return haversine_distance(lat1, lon1, lat2, lon2) <= radius_km
