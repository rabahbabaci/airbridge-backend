"""FastAPI dependencies for optional and required JWT authentication."""

import logging

import jwt
from fastapi import Depends, Header, HTTPException

from app.core.config import settings
from app.db import get_db
from app.db.models import User

logger = logging.getLogger(__name__)


async def get_current_user(
    authorization: str | None = Header(None),
    db=Depends(get_db),
) -> User | None:
    """Decode Bearer JWT and return the User, or None on any failure (optional auth)."""
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception:
        return None

    user_id = payload.get("user_id")
    if not user_id or db is None:
        return None

    try:
        user = await db.get(User, user_id)
        return user
    except Exception:
        logger.exception("Failed to fetch user %s from DB", user_id)
        return None


# Alias — same function serves as optional auth (returns None instead of raising)
get_optional_user = get_current_user


async def get_required_user(
    authorization: str | None = Header(None),
    db=Depends(get_db),
) -> User:
    """Decode Bearer JWT and return the User, or raise 401 if not authenticated."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization[7:]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = payload.get("user_id")
    if not user_id or db is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        user = await db.get(User, user_id)
    except Exception:
        logger.exception("Failed to fetch user %s from DB", user_id)
        raise HTTPException(status_code=401, detail="Authentication required")

    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    return user
