"""Tests for GET /v1/flights/search (airport departure search)."""

from unittest.mock import patch

from fastapi.testclient import TestClient


def _make_fids_departure(
    flight_number: str,
    dest_iata: str,
    dep_local: str,
    airline_name: str = "United Airlines",
    status: str = "Scheduled",
    dest_name: str | None = None,
) -> dict:
    """Build a mock AeroDataBox FIDS departure object (movement-based format)."""
    dest_airport = {"name": dest_name or f"{dest_iata} Airport"}
    if dest_iata:
        dest_airport["iata"] = dest_iata
    return {
        "number": flight_number,
        "status": status,
        "airline": {"name": airline_name},
        "aircraft": {"model": "Boeing 737"},
        "movement": {
            "airport": dest_airport,
            "scheduledTime": {
                "utc": dep_local.replace(" ", "T") + "Z",
                "local": dep_local.replace(" ", "T"),
            },
            "revisedTime": {},
            "terminal": "1",
            "gate": "A1",
        },
    }


# Morning window flights (00:00–11:59)
MORNING_WINDOW = [
    _make_fids_departure("UA300", "LAX", "2026-04-01 08:00", "United Airlines"),
    _make_fids_departure("DL100", "JFK", "2026-04-01 09:00", "Delta Air Lines"),
]

# Afternoon/evening window flights (12:00–23:59)
AFTERNOON_WINDOW = [
    _make_fids_departure("UA400", "LAX", "2026-04-01 14:30", "United Airlines"),
    _make_fids_departure("AA200", "LAX", "2026-04-01 19:00", "American Airlines"),
    _make_fids_departure("UA500", "LAX", "2026-04-01 23:30", "United Airlines"),
    # Flight with no destination IATA — should be skipped
    _make_fids_departure("XX999", "", "2026-04-01 15:00", "Mystery Air", dest_name="San Diego"),
]


def _mock_get(url, **kwargs):
    """Return different data for the two 12-hour windows."""

    class MockResponse:
        status_code = 200

        def __init__(self, departures):
            self._departures = departures

        def json(self):
            return {"departures": self._departures}

    if "T00:00" in url:
        return MockResponse(MORNING_WINDOW)
    else:
        return MockResponse(AFTERNOON_WINDOW)


class TestFlightSearch:
    def test_search_returns_filtered_by_destination(self, client: TestClient):
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = _mock_get

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "LAX",
                "date": "2026-04-01",
            })

        assert resp.status_code == 200
        flights = resp.json()["flights"]
        # LAX-bound: UA300, UA400, AA200, UA500 — not DL100 (JFK) or XX999 (no IATA)
        assert len(flights) == 4
        for f in flights:
            assert f["destination_iata"] == "LAX"

    def test_time_window_morning_filter(self, client: TestClient):
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = _mock_get

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
            mock_cls.return_value.get = _mock_get

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
            mock_cls.return_value.get = _mock_get

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

    def test_missing_iata_flights_are_skipped(self, client: TestClient):
        """Flights without a destination IATA code are silently excluded."""
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = _mock_get

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "LAX",
                "date": "2026-04-01",
            })

        flights = resp.json()["flights"]
        flight_numbers = [f["flight_number"] for f in flights]
        assert "XX999" not in flight_numbers

    def test_origin_iata_set_from_query(self, client: TestClient):
        """The origin_iata field should be set to the queried airport."""
        with patch("app.services.integrations.aerodatabox.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = lambda s: s
            mock_cls.return_value.__exit__ = lambda s, *a: None
            mock_cls.return_value.get = _mock_get

            resp = client.get("/v1/flights/search", params={
                "origin": "SFO",
                "destination": "LAX",
                "date": "2026-04-01",
            })

        flights = resp.json()["flights"]
        for f in flights:
            assert f["origin_iata"] == "SFO"
