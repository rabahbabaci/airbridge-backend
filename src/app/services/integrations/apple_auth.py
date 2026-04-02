"""Apple Sign In identity token verification.

Fetches Apple's JWKS public keys, caches them for 24 hours,
and verifies Apple identity tokens (JWTs signed with RS256).
"""

import logging
import time
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_AUDIENCE = "live.airbridge.app"
JWKS_CACHE_TTL = 86400  # 24 hours


@dataclass
class _JWKSCache:
    client: PyJWKClient | None = None
    fetched_at: float = 0.0


_cache = _JWKSCache()


def _get_jwks_client() -> PyJWKClient:
    now = time.time()
    if _cache.client is None or (now - _cache.fetched_at) > JWKS_CACHE_TTL:
        _cache.client = PyJWKClient(APPLE_JWKS_URL)
        _cache.fetched_at = now
    return _cache.client


@dataclass
class AppleTokenClaims:
    sub: str
    email: str | None = None
    email_verified: bool = False


def verify_apple_identity_token(id_token: str) -> AppleTokenClaims:
    """Decode and verify an Apple identity token.

    Raises jwt.PyJWTError or its subclasses on failure.
    """
    jwks_client = _get_jwks_client()
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)

    payload = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=APPLE_AUDIENCE,
        issuer=APPLE_ISSUER,
    )

    return AppleTokenClaims(
        sub=payload["sub"],
        email=payload.get("email"),
        email_verified=payload.get("email_verified", False),
    )
