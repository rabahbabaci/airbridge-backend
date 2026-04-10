"""Phone OTP and social authentication via Supabase with JWT token issuance."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.rate_limit import limiter
from app.db import get_db
from app.db.models import User
from app.services.integrations.apple_auth import verify_apple_identity_token

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


def _generate_jwt(user_id: str, **extra_claims) -> str:
    payload = {
        "user_id": user_id,
        **extra_claims,
        "exp": datetime.now(tz=timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _compute_tier(trip_count: int, subscription_status: str) -> str:
    return "pro" if trip_count <= 3 or subscription_status == "active" else "free"


# --- Request/Response schemas ---


class SendOtpRequest(BaseModel):
    phone_number: str = Field(..., min_length=1)


class VerifyOtpRequest(BaseModel):
    phone_number: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class SocialAuthRequest(BaseModel):
    provider: Literal["apple", "google"]
    id_token: str = Field(..., min_length=1)
    display_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None


# --- Endpoints ---


@router.post("/send-otp", status_code=200)
@limiter.limit("10/minute")
async def send_otp(request: Request, body: SendOtpRequest):
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
@limiter.limit("10/minute")
async def verify_otp(request: Request, body: VerifyOtpRequest, db=Depends(get_db)):
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
    display_name = None
    email = None

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
        display_name = row.display_name
        email = row.email

    token = _generate_jwt(user_id, phone_number=body.phone_number)
    tier = _compute_tier(trip_count, subscription_status)

    return {
        "user_id": user_id,
        "token": token,
        "trip_count": trip_count,
        "tier": tier,
        "display_name": display_name,
        "email": email,
    }


@router.post("/social", status_code=200)
@limiter.limit("10/minute")
async def social_auth(request: Request, body: SocialAuthRequest, db=Depends(get_db)):
    if body.provider == "apple":
        return await _apple_social_auth(body, db)
    return await _google_social_auth(body, db)


async def _google_social_auth(body: SocialAuthRequest, db):
    client = _get_supabase()
    if client is None:
        raise HTTPException(status_code=503, detail="Auth service not configured")

    try:
        result = client.auth.sign_in_with_id_token({
            "provider": body.provider,
            "token": body.id_token,
        })
        if not result or not result.user:
            raise HTTPException(status_code=401, detail="Authentication failed")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Supabase social auth failed for provider=%s", body.provider)
        raise HTTPException(status_code=401, detail="Authentication failed")

    supabase_user = result.user
    email = getattr(supabase_user, "email", None)
    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by auth provider")

    # Extract display name from provider metadata (Google uses full_name/name)
    user_metadata = getattr(supabase_user, "user_metadata", None) or {}
    provider_name = (
        user_metadata.get("full_name")
        or user_metadata.get("name")
        or body.display_name
    )
    logger.info(
        "Social auth user_metadata for provider=%s email=%s: %s",
        body.provider, email, user_metadata,
    )

    user_id = None
    trip_count = 0
    subscription_status = "none"
    display_name = provider_name

    if db is not None:
        stmt = select(User).where(User.email == email)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = User(
                email=email,
                auth_provider=body.provider,
                display_name=provider_name,
                trip_count=0,
                subscription_status="none",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        else:
            if row.auth_provider is None:
                row.auth_provider = body.provider
            if row.display_name is None and provider_name:
                row.display_name = provider_name
            await db.commit()
            await db.refresh(row)
        user_id = str(row.id)
        trip_count = row.trip_count
        subscription_status = row.subscription_status
        display_name = row.display_name

    token = _generate_jwt(user_id, email=email)
    tier = _compute_tier(trip_count, subscription_status)

    return {
        "user_id": user_id,
        "token": token,
        "trip_count": trip_count,
        "tier": tier,
        "display_name": display_name,
        "email": email,
    }


async def _apple_social_auth(body: SocialAuthRequest, db):
    try:
        claims = verify_apple_identity_token(body.id_token)
    except Exception:
        logger.exception("Apple token verification failed")
        raise HTTPException(status_code=401, detail="Authentication failed")

    apple_sub = claims.sub
    email = claims.email

    # Build display name from frontend-supplied name fields (Apple first sign-in only)
    provider_name = None
    if body.given_name or body.family_name:
        parts = [p for p in (body.given_name, body.family_name) if p]
        provider_name = " ".join(parts) if parts else None

    user_id = None
    trip_count = 0
    subscription_status = "none"
    display_name = provider_name

    if db is not None:
        row = None

        # 1. Look up by email first (links with existing Google accounts)
        if email:
            stmt = select(User).where(User.email == email)
            row = (await db.execute(stmt)).scalar_one_or_none()

        # 2. Fallback: look up by apple_user_id
        if row is None:
            stmt = select(User).where(User.apple_user_id == apple_sub)
            row = (await db.execute(stmt)).scalar_one_or_none()

        if row is None:
            # Create new user
            row = User(
                email=email,
                apple_user_id=apple_sub,
                auth_provider="apple",
                display_name=provider_name,
                trip_count=0,
                subscription_status="none",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        else:
            # Update existing user — set apple_user_id if missing
            if row.apple_user_id is None:
                row.apple_user_id = apple_sub
            if row.auth_provider is None:
                row.auth_provider = "apple"
            # Only update display_name if the user doesn't have one yet
            if row.display_name is None and provider_name:
                row.display_name = provider_name
            await db.commit()
            await db.refresh(row)

        user_id = str(row.id)
        trip_count = row.trip_count
        subscription_status = row.subscription_status
        display_name = row.display_name
        email = row.email  # Use stored email if token didn't have one

    token = _generate_jwt(user_id, email=email)
    tier = _compute_tier(trip_count, subscription_status)

    return {
        "user_id": user_id,
        "token": token,
        "trip_count": trip_count,
        "tier": tier,
        "display_name": display_name,
        "email": email,
    }
