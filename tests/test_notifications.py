"""Tests for notification helper functions."""

from datetime import datetime, timedelta, timezone

from app.services.notifications import is_pro_user, should_notify_leave_by_shift


class FakeUser:
    def __init__(self, trip_count=0, subscription_status="none"):
        self.trip_count = trip_count
        self.subscription_status = subscription_status


def test_should_notify_leave_by_shift_first_time():
    new = datetime.now(tz=timezone.utc)
    assert should_notify_leave_by_shift(None, new) is True


def test_should_notify_leave_by_shift_big_change():
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(minutes=15)
    assert should_notify_leave_by_shift(old, now) is True


def test_should_notify_leave_by_shift_small_change():
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(minutes=5)
    assert should_notify_leave_by_shift(old, now) is False


def test_is_pro_user_trial():
    user = FakeUser(trip_count=2, subscription_status="none")
    assert is_pro_user(user) is True


def test_is_pro_user_subscribed():
    user = FakeUser(trip_count=10, subscription_status="active")
    assert is_pro_user(user) is True


def test_is_pro_user_free():
    user = FakeUser(trip_count=5, subscription_status="none")
    assert is_pro_user(user) is False
