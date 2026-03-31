"""SQLAlchemy ORM models for all persistent tables."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Airport(Base):
    __tablename__ = "airports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    iata_code: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    icao_code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_category: Mapped[str] = mapped_column(String, nullable=False)
    capability_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    has_live_tsa_feed: Mapped[bool] = mapped_column(Boolean, default=False)
    curb_to_checkin: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkin_to_security: Mapped[int | None] = mapped_column(Integer, nullable=True)
    security_to_gate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parking_to_terminal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transit_to_terminal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    phone_number: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    auth_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    trip_count: Mapped[int] = mapped_column(Integer, default=0)
    subscription_status: Mapped[str] = mapped_column(String, default="none")
    preferred_transport_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_security_access: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_bag_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_children: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    preferred_nav_app: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_rideshare_app: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    trips: Mapped[list["Trip"]] = relationship(back_populates="user")
    device_tokens: Mapped[list["DeviceToken"]] = relationship(back_populates="user")
    feedbacks: Mapped[list["Feedback"]] = relationship(back_populates="user")


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    input_mode: Mapped[str] = mapped_column(String, nullable=False)
    flight_number: Mapped[str | None] = mapped_column(String, nullable=True)
    departure_date: Mapped[str] = mapped_column(String, nullable=False)
    home_address: Mapped[str] = mapped_column(String, nullable=False)
    selected_departure_utc: Mapped[str | None] = mapped_column(String, nullable=True)
    preferences_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="created", server_default="created")
    last_pushed_leave_home_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trip_status: Mapped[str] = mapped_column(String, default="created", server_default="created")
    push_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User | None"] = relationship(back_populates="trips")
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="trip"
    )
    feedbacks: Mapped[list["Feedback"]] = relationship(back_populates="trip")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id"), nullable=False
    )
    leave_home_at: Mapped[str] = mapped_column(String, nullable=False)
    gate_arrival_utc: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[str] = mapped_column(String, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(String, nullable=False)
    segments_json: Mapped[str] = mapped_column(Text, nullable=False)
    terminal_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    terminal_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[str] = mapped_column(String, nullable=False)

    trip: Mapped["Trip"] = relationship(back_populates="recommendations")


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    token: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="device_tokens")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    followed_recommendation: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    minutes_at_gate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trust_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_tsa_wait_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    trip: Mapped["Trip"] = relationship(back_populates="feedbacks")
    user: Mapped["User"] = relationship(back_populates="feedbacks")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_name: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
