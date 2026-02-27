"""Schemas for flight snapshot (scheduled times, terminal, airport timing profile)."""

from datetime import datetime

from pydantic import BaseModel, Field


class AirportTimings(BaseModel):
    """Per-airport timing profile for journey segments."""

    curb_to_checkin_minutes: int = Field(
        5,
        description="Walk from terminal curb to check-in/kiosk area",
    )
    parking_to_terminal_minutes: int = Field(
        10,
        description="Walk/shuttle from parking to terminal entrance",
    )
    transit_station_to_terminal_minutes: int = Field(
        12,
        description="Walk from train/bus station to terminal (e.g. BART to SFO)",
    )
    checkin_to_security_minutes: int = Field(
        5,
        description="Walk from check-in area to security entrance",
    )
    security_minutes: int = Field(
        25,
        description="Time through TSA/security screening",
    )
    security_to_gate_minutes: int = Field(
        10,
        description="Walk from security exit to departure gate",
    )


class FlightSnapshot(BaseModel):
    """Snapshot of flight schedule and base airport timings for recommendation logic."""

    scheduled_departure: datetime = Field(..., description="Scheduled departure (UTC)")
    scheduled_arrival: datetime | None = Field(
        None, description="Scheduled arrival (UTC) if known"
    )
    departure_terminal: str | None = Field(
        None, description="Departure terminal if known"
    )
    origin_airport_code: str | None = Field(None, description="Origin IATA code")
    destination_airport_code: str | None = Field(
        None, description="Destination IATA code"
    )
    airport_timings: AirportTimings = Field(
        ..., description="Per-airport timing profile for journey segments"
    )
