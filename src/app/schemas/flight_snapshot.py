"""Schemas for flight snapshot (scheduled times, terminal, base airport timings)."""

from datetime import datetime

from pydantic import BaseModel, Field


class AirportTimings(BaseModel):
    """Baseline times at the airport (e.g. TSA, check-in)."""

    base_tsa_minutes: int = Field(
        ..., description="Baseline TSA/security time in minutes"
    )
    check_in_buffer_minutes: int = Field(
        ..., description="Recommended check-in buffer in minutes"
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
        ..., description="Base airport timings (TSA, check-in)"
    )
