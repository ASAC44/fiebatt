"""Verify Supabase-issued JWTs on incoming requests.

Supabase issues JWTs two ways depending on your project's vintage / settings:

  - HS256 with a shared symmetric secret (legacy / "JWT Secret" mode). Fast,
    no network. Set SUPABASE_JWT_SECRET.
  - RS256 or ES256 with an asymmetric keypair whose public half is served at
    `{supabase_url}/auth/v1/.well-known/jwks.json` (current default on new
    projects). Set SUPABASE_URL.

We try both so either mode works without the app operator having to care.
Falls closed (returns None) on any verification problem → caller treats the
request as anonymous. That keeps offline dev usable even with no auth wired.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient, PyJWKClientError

from app.config.settings import get_settings


log = logging.getLogger("iris.auth")

# PyJWKClient caches keys internally by `kid`, so reusing a single instance
# across requests keeps verification cheap (one HTTP fetch per project
# lifetime under normal conditions). Built lazily once we know the URL.
_jwks_client: Optional[PyJWKClient] = None
_jwks_url: Optional[str] = None


def _get_jwks_client() -> Optional[PyJWKClient]:
    """Return a cached PyJWKClient for the configured Supabase project."""
    global _jwks_client, _jwks_url
    settings = get_settings()
    base = settings.supabase_url.strip().rstrip("/")
    if not base:
        return None
    url = f"{base}/auth/v1/.well-known/jwks.json"
    if _jwks_client is None or _jwks_url != url:
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=3600)
        _jwks_url = url
    return _jwks_client


@dataclass(slots=True)
class AuthedUser:
    id: str
    email: Optional[str]


def _extract_user(claims: dict) -> Optional[AuthedUser]:
    sub = claims.get("sub")
    if not sub:
        return None
    email = claims.get("email") or (claims.get("user_metadata") or {}).get("email")
    return AuthedUser(id=str(sub), email=email)


def verify_supabase_token(token: str) -> Optional[AuthedUser]:
    """Decode + verify a Supabase JWT (HS256 / RS256 / ES256). None on failure."""
    if not token:
        return None

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        log.info("rejected unreadable token header: %s", e)
        return None

    alg = unverified_header.get("alg")
    decode_opts = {"verify_iss": False}

    # HS256 fast path — shared-secret projects.
    if alg == "HS256":
        settings = get_settings()
        secret = settings.supabase_jwt_secret
        if not secret:
            log.info("HS256 token received but SUPABASE_JWT_SECRET is unset")
            return None
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience="authenticated",
                options=decode_opts,
            )
        except jwt.ExpiredSignatureError:
            log.info("rejected expired supabase token")
            return None
        except jwt.InvalidTokenError as e:
            log.info("rejected invalid supabase token: %s", e)
            return None
        return _extract_user(claims)

    # Asymmetric path — fetch the signing key from the project's JWKS.
    if alg in {"RS256", "ES256"}:
        client = _get_jwks_client()
        if client is None:
            log.info(
                "%s token received but SUPABASE_URL is unset — can't fetch JWKS",
                alg,
            )
            return None
        try:
            signing_key = client.get_signing_key_from_jwt(token).key
        except PyJWKClientError as e:
            log.info("failed to fetch supabase jwks: %s", e)
            return None
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[alg],
                audience="authenticated",
                options=decode_opts,
            )
        except jwt.ExpiredSignatureError:
            log.info("rejected expired supabase token")
            return None
        except jwt.InvalidTokenError as e:
            log.info("rejected invalid supabase token: %s", e)
            return None
        return _extract_user(claims)

    log.info("rejected supabase token with unsupported alg %r", alg)
    return None


def extract_bearer(authorization_header: Optional[str]) -> Optional[str]:
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
