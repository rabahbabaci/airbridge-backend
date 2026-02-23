from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TransportMode(str, Enum):
    driving = "driving"
    transit = "transit"
    rideshare = "rideshare"
    walking = "walking"


class TripRequest(BaseModel):
    origin_address: str = Field(..., description="Full home/origin address")
    airport_code: str = Field(..., min_length=3, max_length=4, description="IATA airport code, e.g. JFK")
    flight_number: str = Field(..., description="Flight number, e.g. AA123")
    departure_time: datetime = Field(..., description="Scheduled departure time (UTC)")
    bag_count: int = Field(0, ge=0, description="Number of checked bags")
    children_count: int = Field(0, ge=0, description="Number of children travelling")
    transport_mode: TransportMode = Field(TransportMode.driving, description="Preferred transport mode to airport")


class TripResponse(BaseModel):
    trip_id: str = Field(..., description="Unique trip identifier")
    status: str = Field(..., description="Trip validation status")
    origin_address: str
    airport_code: str
    flight_number: str
    departure_time: datetime
    bag_count: int
    children_count: int
    transport_mode: TransportMode
