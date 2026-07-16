from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.integration import ProviderCredential


PROVIDER_FIELDS = {
    "gemini": "gemini_api_key",
    "dashscope": "dashscope_api_key",
    "mesh": "mesh_api_key",
    "elevenlabs": "elevenlabs_api_key",
}


def _key() -> bytes:
    configured = get_settings().credential_encryption_key.strip()
    if configured:
        padded = configured + "=" * (-len(configured) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw) != 32:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY must encode exactly 32 bytes")
        return raw
    return hashlib.sha256(get_settings().auth_jwt_secret.encode("utf-8")).digest()


def encrypt_secret(secret: str) -> str:
    import os

    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(nonce, secret.encode("utf-8"), b"fiebatt-provider-key-v1")
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_secret(payload: str) -> str:
    raw = base64.urlsafe_b64decode(payload.encode("ascii"))
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], b"fiebatt-provider-key-v1").decode("utf-8")


async def list_provider_status(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        await db.execute(
            select(ProviderCredential)
            .where(ProviderCredential.user_id == user_id)
            .order_by(ProviderCredential.provider)
        )
    ).scalars().all()
    by_provider = {row.provider: row for row in rows}
    return [
        {
            "provider": provider,
            "configured": provider in by_provider,
            "key_hint": by_provider[provider].key_hint if provider in by_provider else "",
            "validated_at": (
                by_provider[provider].validated_at.isoformat()
                if provider in by_provider and by_provider[provider].validated_at
                else None
            ),
        }
        for provider in PROVIDER_FIELDS
    ]


async def set_provider_credential(db: AsyncSession, user_id: str, provider: str, value: str) -> None:
    if provider not in PROVIDER_FIELDS:
        raise ValueError("unsupported provider")
    value = value.strip()
    if len(value) < 8:
        raise ValueError("provider key is too short")
    row = (
        await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProviderCredential(user_id=user_id, provider=provider, encrypted_value="")
        db.add(row)
    row.encrypted_value = encrypt_secret(value)
    row.key_hint = value[-4:]
    row.validated_at = datetime.now(timezone.utc)
    await db.commit()


async def delete_provider_credential(db: AsyncSession, user_id: str, provider: str) -> bool:
    row = (
        await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def provider_overrides(db: AsyncSession, user_id: str) -> dict[str, str]:
    rows = (
        await db.execute(select(ProviderCredential).where(ProviderCredential.user_id == user_id))
    ).scalars().all()
    return {
        PROVIDER_FIELDS[row.provider]: decrypt_secret(row.encrypted_value)
        for row in rows
        if row.provider in PROVIDER_FIELDS
    }
