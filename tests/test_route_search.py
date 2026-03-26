"""Tests for GET /v1/flights/search (airport departure search)."""

from unittest.mock import patch

from fastapi.testclient import TestClient


def _make_raw_departure(
    flight_number: str,
    origin_iata: str,
    dest_iata: str,
    dep_local: str,
    airline_name: str = "United Airlines",
    status: str = "Scheduled",
) -> dict:
    """Build a mock AeroDataBox raw flight object (pre-parse_flight format)."""
    return {
        "number": flight_number,
        "status": status,
        "airline": {"name": airline_name},
        "aircraft": {"model": "Boeing 737"},
        "departure": {
            "airport": {"iata": origin_iata, "name": f"{origin_iata} Airport"},
            "scheduledTime": {"utc": dep_local.replace(" ", "T") + "Z", "local": dep_local.replace(" ", "T")},
            "revisedTime": {},
            "terminal": "1",
            "gate": "A1",
        },
        "arrival": {
            "airport": {"iata": dest_iata, "name": f"{dest_iata} Airport"},
            "scheduledTime": {"utc": dep_local.replace(" ", "T") + "Z", "local": dep_local.replace(" ", "T")},
            "revisedTime": {},
            "terminal": "2",
        },
    }


MOCK_DEPARTURES_RAW = [
    _make_raw_departure("UA300", "SFO", "LAX", "2026-04-01 08:00", "United Airlines"),
    _make_raw_departure("UA400", "SFO", "LAX", "2026-04-01 14:30", "United Airlines"),
    _make_raw_departure("DL100", "SFO", "JFK", "2026-04-01 09:00", "Delta Air Lines"),
    _make_raw_departure("AA200", "SFO", "LAX", "2026-04-01 19:00", "American Airlines"),
    _make_raw_departure("UA500", "SFO", "LAX", "2026-04-01 23:30", "United Airlines"),
]


def _mock_departures_api(iata, from_local, to_local, **kwargs):
    """Mock httpx response for AeroDataBox departures endpoint."""

    class MockResponse:
        status_code = 200

        def json(self):
            return {"departures": MOCK_DEPARTURES_RAW}

    return MockResponse()


class TestFlightSearch:
    def test_search_returns_filtered_by_destination(self, client: TestClient):
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = lambda url, **kw: _mock_departures_api(None, None, None)

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "LAX",
                "date": "2026-04-01",
            })

        assert resp.status_code == 200
        data = resp.json()
        flights = data["flights"]
        # Only LAX-bound flights (UA300, UA400, AA200, UA500) — not DL100 (JFK)
        assert len(flights) == 4
        for f in flights:
            assert f["destination_iata"] == "LAX"

    def test_time_window_morning_filter(self, client: TestClient):
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = lambda url, **kw: _mock_departures_api(None, None, None)

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "LAX",
                "date": "2026-04-01",
                "time_window": "morning",
            })

        assert resp.status_code == 200
        flights = resp.json()["flights"]
        # Only UA300 at 08:00 is in morning (05:00-11:59)
        assert len(flights) == 1
        assert flights[0]["flight_number"] == "UA300"

    def test_airline_filter(self, client: TestClient):
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = lambda url, **kw: _mock_departures_api(None, None, None)

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "LAX",
                "date": "2026-04-01",
                "airline": "AA",
            })

        assert resp.status_code == 200
        flights = resp.json()["flights"]
        assert len(flights) == 1
        assert flights[0]["flight_number"] == "AA200"

    def test_no_matching_flights_returns_empty(self, client: TestClient):
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = lambda url, **kw: _mock_departures_api(None, None, None)

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "ORD",
                "date": "2026-04-01",
            })

        assert resp.status_code == 200
        assert resp.json() == {"flights": []}

    def test_missing_origin_returns_422(self, client: TestClient):
        resp = client.get("/v1/flights/search", params={
            "destination": "LAX",
            "date": "2026-04-01",
        })
        assert resp.status_code == 422
