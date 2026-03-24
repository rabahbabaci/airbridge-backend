"""Phone OTP authentication via Supabase and JWT token issuance."""

import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.db import async_session_factory, get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Lazy Supabase client
_supabase_client = None


def _get_supabase():
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not settings.supabase_url or not settings.supabase_key:
        return None
    from supabase import create_client

    _supabase_client = create_client(settings.supabase_url, settings.supabase_key)
    return _supabase_client


# --- Request/Response schemas ---


class SendOtpRequest(BaseModel):
    phone_number: str = Field(..., min_length=1)


class VerifyOtpRequest(BaseModel):
    phone_number: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


# --- Endpoints ---


@router.post("/send-otp", status_code=200)
async def send_otp(body: SendOtpRequest):
    client = _get_supabase()
    if client is None:
        raise HTTPException(status_code=503, detail="Auth service not configured")

    try:
        client.auth.sign_in_with_otp({"phone": body.phone_number})
    except Exception as e:
        logger.exception("Supabase send-otp failed for %s", body.phone_number)
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": "OTP sent"}


@router.post("/verify-otp", status_code=200)
async def verify_otp(body: VerifyOtpRequest, db=Depends(get_db)):
    client = _get_supabase()
    if client is None:
        raise HTTPException(status_code=503, detail="Auth service not configured")

    try:
        result = client.auth.verify_otp(
            {"phone": body.phone_number, "token": body.code, "type": "sms"}
        )
        if not result or not result.user:
            raise HTTPException(status_code=401, detail="Invalid or expired code")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Supabase verify-otp failed for %s", body.phone_number)
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    # Find or create user
    user_id = None
    trip_count = 0
    subscription_status = "none"

    if db is not None:
        stmt = select(User).where(User.phone_number == body.phone_number)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = User(
                phone_number=body.phone_number,
                trip_count=0,
                subscription_status="none",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        user_id = str(row.id)
        trip_count = row.trip_count
        subscription_status = row.subscription_status

    # Generate JWT
    payload = {
        "user_id": user_id,
        "phone_number": body.phone_number,
        "exp": datetime.now(tz=timezone.utc) + timedelta(days=30),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    tier = (
        "pro"
        if trip_count <= 3 or subscription_status == "active"
        else "free"
    )

    return {
        "user_id": user_id,
        "token": token,
        "trip_count": trip_count,
        "tier": tier,
    }
