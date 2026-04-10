"""User profile and preferences endpoints."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.middleware.auth import get_required_user
from app.db import get_db
from app.db.models import (
    DeviceToken,
    Event,
    Feedback,
    Recommendation,
    Trip,
    TsaObservation,
    User,
)
from app.services.trial import get_tier_info

router = APIRouter(tags=["users"])


class UpdatePreferencesRequest(BaseModel):
    transport_mode: str | None = Field(None, max_length=20)
    security_access: str | None = Field(None, max_length=20)
    bag_count: int | None = Field(None, ge=0, le=10)
    children: bool | None = None
    nav_app: str | None = Field(None, max_length=50)
    rideshare_app: str | None = Field(None, max_length=50)
    buffer_minutes: int | None = Field(None, ge=0, le=180)


class UserProfileResponse(BaseModel):
    user_id: str
    email: str | None
    phone_number: str | None
    display_name: str | None
    auth_provider: str | None
    trip_count: int
    tier: str
    remaining_pro_trips: int | None
    preferences: dict


PREF_FIELD_MAP = {
    "transport_mode": "preferred_transport_mode",
    "security_access": "preferred_security_access",
    "bag_count": "preferred_bag_count",
    "children": "preferred_children",
    "nav_app": "preferred_nav_app",
    "rideshare_app": "preferred_rideshare_app",
}


def _build_preferences(user: User) -> dict:
    return {
        "transport_mode": user.preferred_transport_mode,
        "security_access": user.preferred_security_access,
        "bag_count": user.preferred_bag_count,
        "children": user.preferred_children,
        "nav_app": user.preferred_nav_app,
        "rideshare_app": user.preferred_rideshare_app,
    }


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    if db is not None:
        await db.refresh(user)

    tier, remaining = get_tier_info(user)

    return UserProfileResponse(
        user_id=str(user.id),
        email=user.email,
        phone_number=user.phone_number,
        display_name=user.display_name,
        auth_provider=user.auth_provider,
        trip_count=user.trip_count,
        tier=tier,
        remaining_pro_trips=remaining,
        preferences=_build_preferences(user),
    )


@router.put("/preferences")
async def update_preferences(
    body: UpdatePreferencesRequest,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    if db is None:
        # In-memory mode: echo back the input as preferences
        return {k: v for k, v in body.model_dump().items() if v is not None}

    for api_field, model_attr in PREF_FIELD_MAP.items():
        value = getattr(body, api_field)
        if value is not None:
            setattr(user, model_attr, value)

    await db.commit()
    await db.refresh(user)
    return _build_preferences(user)


@router.delete("/me", status_code=204)
async def delete_account(
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """Delete user account and all associated data."""
    if db is None:
        return Response(status_code=204)

    # Cascade delete in dependency order
    await db.execute(delete(Event).where(Event.user_id == user.id))
    await db.execute(delete(DeviceToken).where(DeviceToken.user_id == user.id))
    await db.execute(delete(TsaObservation).where(TsaObservation.user_id == user.id))
    await db.execute(delete(Feedback).where(Feedback.user_id == user.id))

    # Get user's trip IDs for recommendation cascade
    trip_ids = (
        await db.execute(select(Trip.id).where(Trip.user_id == user.id))
    ).scalars().all()

    if trip_ids:
        await db.execute(delete(Recommendation).where(Recommendation.trip_id.in_(trip_ids)))
        await db.execute(delete(Feedback).where(Feedback.trip_id.in_(trip_ids)))

    await db.execute(delete(Trip).where(Trip.user_id == user.id))
    await db.delete(user)
    await db.commit()

    return Response(status_code=204)
