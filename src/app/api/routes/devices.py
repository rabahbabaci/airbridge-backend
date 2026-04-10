"""Device token registration endpoints for push notifications."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.middleware.auth import get_required_user
from app.db import get_db
from app.db.models import DeviceToken, User

router = APIRouter(tags=["devices"])

VALID_PLATFORMS = ("ios", "android", "web")


class RegisterDeviceRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=500)
    platform: Literal["ios", "android", "web"]


class UnregisterDeviceRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=500)


@router.post("/register", status_code=200)
async def register_device(
    body: RegisterDeviceRequest,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    if db is None:
        return {"status": "registered", "platform": body.platform}

    # Check for duplicate
    stmt = select(DeviceToken).where(
        DeviceToken.user_id == user.id,
        DeviceToken.token == body.token,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is None:
        device = DeviceToken(
            user_id=user.id,
            token=body.token,
            platform=body.platform,
        )
        db.add(device)
        await db.commit()

    return {"status": "registered", "platform": body.platform}


@router.delete("/unregister", status_code=200)
async def unregister_device(
    body: UnregisterDeviceRequest,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    if db is None:
        return {"status": "unregistered"}

    stmt = select(DeviceToken).where(
        DeviceToken.user_id == user.id,
        DeviceToken.token == body.token,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()

    return {"status": "unregistered"}
