from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class TransportMode(str, Enum):
    rideshare = "rideshare"
    driving = "driving"
    train = "train"
    bus = "bus"
    other = "other"


class ConfidenceProfile(str, Enum):
    safety = "safety"
    sweet = "sweet"
    risk = "risk"


class SecurityAccess(str, Enum):
    none = "none"
    precheck = "precheck"
    clear = "clear"
    clear_precheck = "clear_precheck"
    priority_lane = "priority_lane"


ExtraTimeMinutes = Literal[0, 15, 30]
EXTRA_TIME_MINUTES_VALUES: tuple[int, ...] = (0, 15, 30)


class TripPreferenceOverrides(BaseModel):
    """All-optional version of TripPreferences for selective overrides."""

    transport_mode: TransportMode | None = None
    confidence_profile: ConfidenceProfile | None = None
    bag_count: int | None = None
    traveling_with_children: bool | None = None
    extra_time_minutes: int | None = None  # 0, 15, or 30
    has_boarding_pass: bool | None = None
    security_access: SecurityAccess | None = None

    @field_validator("bag_count")
    @classmethod
    def bag_count_range(cls, v: int | None) -> int | None:
        if v is not None and (v < 0 or v > 3):
            raise ValueError("bag_count must be between 0 and 3")
        return v

    @field_validator("extra_time_minutes")
    @classmethod
    def extra_time_values(cls, v: object) -> int | None:
        if v is None:
            return None
        if isinstance(v, int) and v in EXTRA_TIME_MINUTES_VALUES:
            return v
        if isinstance(v, str) and v.strip().isdigit():
            n = int(v.strip())
            if n in EXTRA_TIME_MINUTES_VALUES:
                return n
        raise ValueError("extra_time_minutes must be 0, 15, or 30")


class TripPreferences(BaseModel):
    transport_mode: TransportMode = TransportMode.driving
    confidence_profile: ConfidenceProfile = ConfidenceProfile.sweet
    bag_count: int = Field(0, ge=0, le=3)
    traveling_with_children: bool = False
    extra_time_minutes: ExtraTimeMinutes = 0
    has_boarding_pass: bool = True
    security_access: SecurityAccess = SecurityAccess.none

    @field_validator("bag_count", mode="before")
    @classmethod
    def bag_count_range(cls, v: object) -> int:
        if v is None:
            return 0
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and (v < 0 or v > 3):
            raise ValueError("bag_count must be between 0 and 3")
        if not isinstance(v, int):
            raise ValueError("bag_count must be an integer")
        return v

    @field_validator("extra_time_minutes", mode="before")
    @classmethod
    def extra_time_values(cls, v: object) -> int:
        if v is None:
            return 0
        if isinstance(v, int) and v in EXTRA_TIME_MINUTES_VALUES:
            return v
        if isinstance(v, str) and v.strip().isdigit():
            n = int(v.strip())
            if n in EXTRA_TIME_MINUTES_VALUES:
                return n
        raise ValueError("extra_time_minutes must be 0, 15, or 30")


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
    departure_date: date = Field(
        ..., description="Scheduled departure date (YYYY-MM-DD)"
    )
    home_address: str = Field(..., description="Full home/origin address")
    preferences: TripPreferences = Field(default_factory=TripPreferences)

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
    airline: str = Field(
        ..., description="Airline name or IATA code, e.g. AA or American Airlines"
    )
    origin_airport: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="IATA origin airport code, e.g. JFK",
    )
    destination_airport: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="IATA destination airport code, e.g. LAX",
    )
    departure_date: date = Field(
        ..., description="Scheduled departure date (YYYY-MM-DD)"
    )
    departure_time_window: DepartureTimeWindow = Field(
        ..., description="Preferred departure time window"
    )
    home_address: str = Field(..., description="Full home/origin address")
    preferences: TripPreferences = Field(default_factory=TripPreferences)

    @field_validator("origin_airport", "destination_airport", mode="before")
    @classmethod
    def normalize_airport_code(cls, v: str) -> str:
        normalized = _normalize_iata(v)
        if not normalized.isalpha() or len(normalized) != 3:
            raise ValueError(
                "Airport code must be exactly 3 alphabetic characters (IATA format)."
            )
        return normalized

    @field_validator("airline", "home_address", mode="before")
    @classmethod
    def normalize_string_fields(cls, v: str) -> str:
        return _normalize_str(v)

    @model_validator(mode="after")
    def origin_and_destination_differ(self) -> "RouteSearchTripRequest":
        if self.origin_airport == self.destination_airport:
            raise ValueError(
                "origin_airport and destination_airport must be different."
            )
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

    preferences: TripPreferences = Field(default_factory=TripPreferences)

    # flight_number mode fields
    flight_number: str | None = None

    # route_search mode fields
    airline: str | None = None
    origin_airport: str | None = None
    destination_airport: str | None = None
    departure_time_window: DepartureTimeWindow | None = None
