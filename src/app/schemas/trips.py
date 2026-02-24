from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class DepartureTimeWindow(str, Enum):
    morning = "morning"
    midday = "midday"
    afternoon = "afternoon"
    evening = "evening"
    late_night = "late_night"
    not_sure = "not_sure"


def _normalize_str(v: str) -> str:
    return v.strip()


def _normalize_iata(v: str) -> str:
    return v.strip().upper()


class FlightNumberTripRequest(BaseModel):
    input_mode: Literal["flight_number"]
    flight_number: str = Field(..., description="Flight number, e.g. AA123")
    departure_date: date = Field(..., description="Scheduled departure date (YYYY-MM-DD)")
    home_address: str = Field(..., description="Full home/origin address")

    @field_validator("flight_number", mode="before")
    @classmethod
    def normalize_flight_number(cls, v: str) -> str:
        return _normalize_str(v).upper()

    @field_validator("home_address", mode="before")
    @classmethod
    def normalize_home_address(cls, v: str) -> str:
        return _normalize_str(v)


class RouteSearchTripRequest(BaseModel):
    input_mode: Literal["route_search"]
    airline: str = Field(..., description="Airline name or IATA code, e.g. AA or American Airlines")
    origin_airport: str = Field(..., min_length=3, max_length=3, description="IATA origin airport code, e.g. JFK")
    destination_airport: str = Field(..., min_length=3, max_length=3, description="IATA destination airport code, e.g. LAX")
    departure_date: date = Field(..., description="Scheduled departure date (YYYY-MM-DD)")
    departure_time_window: DepartureTimeWindow = Field(..., description="Preferred departure time window")
    home_address: str = Field(..., description="Full home/origin address")

    @field_validator("origin_airport", "destination_airport", mode="before")
    @classmethod
    def normalize_airport_code(cls, v: str) -> str:
        normalized = _normalize_iata(v)
        if not normalized.isalpha() or len(normalized) != 3:
            raise ValueError("Airport code must be exactly 3 alphabetic characters (IATA format).")
        return normalized

    @field_validator("airline", "home_address", mode="before")
    @classmethod
    def normalize_string_fields(cls, v: str) -> str:
        return _normalize_str(v)

    @model_validator(mode="after")
    def origin_and_destination_differ(self) -> "RouteSearchTripRequest":
        if self.origin_airport == self.destination_airport:
            raise ValueError("origin_airport and destination_airport must be different.")
        return self


TripRequest = Annotated[
    Union[FlightNumberTripRequest, RouteSearchTripRequest],
    Field(discriminator="input_mode"),
]


class TripContext(BaseModel):
    """Normalized trip context returned after successful intake."""

    trip_id: UUID
    input_mode: str
    departure_date: date
    home_address: str
    created_at: datetime
    status: Literal["validated"] = "validated"

    # flight_number mode fields
    flight_number: str | None = None

    # route_search mode fields
    airline: str | None = None
    origin_airport: str | None = None
    destination_airport: str | None = None
    departure_time_window: DepartureTimeWindow | None = None
