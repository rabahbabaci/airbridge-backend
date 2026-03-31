"""Tests for trip state machine."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.trip_state import advance_status, should_activate


class FakeTrip:
    def __init__(self, status="created", trip_status="created", selected_departure_utc=None, departure_date=None):
        self.status = status
        self.trip_status = trip_status
        self.selected_departure_utc = selected_departure_utc
        self.departure_date = departure_date


def test_should_activate_within_24h():
    dep = datetime.now(tz=timezone.utc) + timedelta(hours=12)
    trip = FakeTrip(selected_departure_utc=dep.isoformat())
    assert should_activate(trip, datetime.now(tz=timezone.utc)) is True


def test_should_not_activate_far_future():
    dep = datetime.now(tz=timezone.utc) + timedelta(hours=48)
    trip = FakeTrip(selected_departure_utc=dep.isoformat())
    assert should_activate(trip, datetime.now(tz=timezone.utc)) is False


def test_advance_status_valid():
    trip = FakeTrip()
    advance_status(trip, "active")
    assert trip.status == "active"
    assert trip.trip_status == "active"


def test_advance_status_invalid():
    trip = FakeTrip(status="active", trip_status="active")
    with pytest.raises(ValueError, match="only forward transitions"):
        advance_status(trip, "created")
