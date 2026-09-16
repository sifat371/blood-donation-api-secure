"""
Donor eligibility logic.

Rule (§2.7):
  eligible = is_available AND (last_donation_date is None OR days_since >= 90)
"""

from datetime import date
from typing import Optional

from app.db.models import User
from app.core.time import business_today

ELIGIBILITY_DAYS = 90


def is_eligible(user: User) -> bool:
    """Return True if the user is eligible to donate blood right now."""
    if not user.is_available:
        return False
    if user.last_donation_date is None:
        return True
    return days_since_last_donation(user.last_donation_date) >= ELIGIBILITY_DAYS


def days_since_last_donation(last_donation_date: date) -> int:
    """Days elapsed since the last donation."""
    return (business_today() - last_donation_date).days


def days_until_eligible(user: User) -> int:
    """
    Days remaining until the user becomes eligible.
    Returns 0 if already eligible.
    """
    if user.last_donation_date is None:
        return 0
    elapsed = days_since_last_donation(user.last_donation_date)
    remaining = ELIGIBILITY_DAYS - elapsed
    return max(0, remaining)


# Red-cell donor compatibility by recipient blood group. This is a software
# matching rule only; final transfusion suitability still requires blood-bank
# testing and clinical screening.
RBC_COMPATIBLE_DONORS = {
    "O-": frozenset({"O-"}),
    "O+": frozenset({"O-", "O+"}),
    "A-": frozenset({"O-", "A-"}),
    "A+": frozenset({"O-", "O+", "A-", "A+"}),
    "B-": frozenset({"O-", "B-"}),
    "B+": frozenset({"O-", "O+", "B-", "B+"}),
    "AB-": frozenset({"O-", "A-", "B-", "AB-"}),
    "AB+": frozenset({"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"}),
}


def is_blood_compatible(donor_group: Optional[str], recipient_group: Optional[str]) -> bool:
    """Return whether donor red cells are compatible with the recipient group."""
    if not donor_group or not recipient_group:
        return False
    return donor_group in RBC_COMPATIBLE_DONORS.get(recipient_group, frozenset())
