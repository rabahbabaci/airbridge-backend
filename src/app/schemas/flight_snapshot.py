"""Schemas for flight snapshot (scheduled times, terminal, gate, local hour)."""

from datetime import datetime

from pydantic import BaseModel, Field


class FlightSnapshot(BaseModel):
    """Snapshot of flight schedule for recommendation logic."""

    scheduled_departure: datetime = Field(..., description="Scheduled departure (UTC, or revised if delayed)")
    departure_terminal: str | None = Field(
        None, description="Departure terminal if known"
    )
    departure_gate: str | None = Field(
        None, description="Departure gate if known"
    )
    origin_airport_code: str | None = Field(None, description="Origin IATA code")
    departure_local_hour: int | None = Field(
        None, description="Local hour of departure (0-23) for TSA estimates"
    )
