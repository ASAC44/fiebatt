"""First-party JWT and password helpers for fiebatt auth."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from app.config.settings import get_settings


@dataclass(slots=True)
class AuthedUser:
    id: str
    email: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        210_000,
    )
    return "pbkdf2_sha256$210000$%s$%s" % (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, rounds_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_raw.encode("ascii"))
        expected = base64.b64decode(digest_raw.encode("ascii"))
        rounds = int(rounds_raw)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        rounds,
    )
    return hmac.compare_digest(actual, expected)


def create_access_token(
    user: AuthedUser,
    *,
    scopes: list[str] | None = None,
    audience: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(
        minutes=expires_minutes or settings.auth_jwt_expires_minutes
    )
    claims: dict[str, Any] = {
        "sub": user.id,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if scopes:
        claims["scope"] = " ".join(scopes)
    if audience:
        claims["aud"] = audience
    return jwt.encode(
        claims,
        settings.auth_jwt_secret,
        algorithm="HS256",
    )


def verify_access_token(token: str) -> Optional[AuthedUser]:
    if not token:
        return None

    try:
        claims = jwt.decode(
            token,
            get_settings().auth_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.InvalidTokenError:
        return None

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        return None
    return AuthedUser(id=str(sub), email=str(email))


def decode_access_token(token: str, *, audience: str | None = None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        claims = jwt.decode(
            token,
            get_settings().auth_jwt_secret,
            algorithms=["HS256"],
            audience=audience,
            options={"verify_aud": audience is not None},
        )
    except jwt.InvalidTokenError:
        return None
    if not claims.get("sub") or not claims.get("email"):
        return None
    return dict(claims)


def extract_bearer(authorization_header: Optional[str]) -> Optional[str]:
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
