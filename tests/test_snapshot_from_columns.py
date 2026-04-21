"""Unit tests for snapshot_from_columns — Phase 2 reconstruction helper."""

from app.services.flight_snapshot_service import snapshot_from_columns


def _info(**overrides):
    base = {
        "airline": "United Airlines",
        "flight_number": "UA100",
        "origin_iata": "SFO",
        "destination_iata": "ORD",
        "scheduled_departure_at": "2099-01-01T18:00:00+00:00",
        "scheduled_arrival_at": "2099-01-01T22:00:00+00:00",
        "aircraft_type": "B737",
        "terminal": "8",
        "duration_minutes": 240,
        "departure_local_hour": 10,
        "snapshot_taken_at": "2026-04-01T12:00:00+00:00",
    }
    base.update(overrides)
    return base


def _status(**overrides):
    base = {
        "gate": "B14",
        "status": "Scheduled",
        "delay_minutes": 0,
        "actual_departure_at": None,
        "cancelled": False,
        "last_updated_at": "2026-04-01T12:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestSnapshotFromColumns:
    def test_happy_path_builds_snapshot(self):
        snap = snapshot_from_columns(_info(), _status())
        assert snap is not None
        assert snap.origin_airport_code == "SFO"
        assert snap.departure_terminal == "8"
        assert snap.departure_gate == "B14"  # from flight_status (live)
        assert snap.departure_local_hour == 10
        assert snap.scheduled_departure.hour == 18

    def test_returns_none_when_flight_info_missing(self):
        assert snapshot_from_columns(None, _status()) is None
        assert snapshot_from_columns({}, _status()) is None

    def test_returns_none_when_scheduled_departure_unparseable(self):
        assert snapshot_from_columns(_info(scheduled_departure_at=None), _status()) is None
        assert snapshot_from_columns(_info(scheduled_departure_at="garbage"), _status()) is None

    def test_returns_none_when_origin_iata_missing(self):
        assert snapshot_from_columns(_info(origin_iata=None), _status()) is None
        assert snapshot_from_columns(_info(origin_iata=""), _status()) is None

    def test_gate_is_none_when_flight_status_missing(self):
        snap = snapshot_from_columns(_info(), None)
        assert snap is not None
        assert snap.departure_gate is None

    def test_gate_is_none_when_flight_status_lacks_gate(self):
        snap = snapshot_from_columns(_info(), _status(gate=None))
        assert snap is not None
        assert snap.departure_gate is None
