"""Reverse trial tier logic."""


def is_pro(user) -> bool:
    if user is None:
        return False
    return user.trip_count <= 3 or user.subscription_status == "active"


def get_tier_info(user) -> tuple[str, int | None]:
    """Returns (tier, remaining_pro_trips)."""
    if user is None:
        return ("free", None)
    if user.subscription_status == "active":
        return ("pro", None)
    if user.trip_count <= 3:
        return ("pro", 3 - user.trip_count)
    return ("free", 0)
