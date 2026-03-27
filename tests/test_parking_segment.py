"""Test that driving trips include a parking segment with positive duration."""

from fastapi.testclient import TestClient

TRIP_PAYLOAD = {
    "input_mode": "flight_number",
    "flight_number": "AA123",
    "departure_date": "2026-06-01",
    "home_address": "123 Main St, San Francisco, CA 94105",
    "preferences": {
        "transport_mode": "driving",
    },
}


def _create_driving_trip(client: TestClient) -> str:
    r = client.post("/v1/trips", json=TRIP_PAYLOAD)
    assert r.status_code == 201
    return r.json()["trip_id"]


class TestParkingSegment:
    def test_driving_trip_includes_parking_segment(self, client: TestClient) -> None:
        """A driving trip to SFO should include a parking segment with duration > 0."""
        trip_id = _create_driving_trip(client)
        resp = client.post("/v1/recommendations", json={"trip_id": trip_id})
        assert resp.status_code == 200

        data = resp.json()
        segments = data["segments"]
        parking_segments = [s for s in segments if s["id"] == "parking"]

        assert len(parking_segments) == 1, (
            f"Expected exactly 1 parking segment, got {len(parking_segments)}. "
            f"Segment ids: {[s['id'] for s in segments]}"
        )

        parking = parking_segments[0]
        assert parking["label"] == "Parking"
        assert parking["duration_minutes"] > 0
        assert "Park & walk to terminal" in parking["advice"]
