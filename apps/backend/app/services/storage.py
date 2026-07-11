"""Storage layer — Vultr Object Storage with a local scratch fallback.

Everything ffmpeg touches needs a real file path, so we always round-trip
through a local scratch directory (settings.storage_path). In S3 mode:
  • save_upload/write_bytes/publish upload the scratch file to Vultr
  • url_for_path mints a presigned GET url (or public url, per settings)
  • path_from_url downloads on miss, then returns the cached local path

If S3 env vars aren't set, everything falls back to local disk + /media
URLs (the pre-Vultr behaviour) so offline dev still works.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlsplit

import aiofiles
import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import UploadFile

from app.config.settings import get_settings


log = logging.getLogger("iris.storage")
settings = get_settings()


# ── s3 client (lazy, cached) ────────────────────────────────────────

_s3_client = None


def _client():
    global _s3_client
    if _s3_client is None and settings.s3_enabled:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.vultr_s3_endpoint,
            region_name=settings.vultr_s3_region,
            aws_access_key_id=settings.vultr_s3_access_key,
            aws_secret_access_key=settings.vultr_s3_secret_key,
            # virtual-hosted-style (iris.sjc1.vultrobjects.com) works on
            # vultr, but path-style is friendlier with arbitrary endpoints.
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return _s3_client


# ── naming helpers ──────────────────────────────────────────────────


def _new_name(ext: str) -> str:
    return f"{uuid.uuid4().hex}.{ext.lstrip('.')}"


def _key_from_path(p: Path) -> str:
    """Turn a scratch path under storage_path into an S3 object key."""
    return p.resolve().relative_to(settings.storage_path.resolve()).as_posix()


def key_from_url(url: str) -> Optional[str]:
    """Public alias of `_key_from_url` for callers outside this module."""
    return _key_from_url(url)


def _key_from_url(url: str) -> Optional[str]:
    """Best-effort: pull the S3 object key out of any URL we might have minted.

    Handles:
      • /media/clips/abc.mp4            → clips/abc.mp4  (legacy local-mode)
      • https://bucket.host/clips/abc.mp4?...   → clips/abc.mp4
      • https://host/bucket/clips/abc.mp4?...   → clips/abc.mp4
      • s3://bucket/clips/abc.mp4       → clips/abc.mp4
      • clips/abc.mp4                   → clips/abc.mp4
    """
    if not url:
        return None
    if url.startswith("/media/"):
        return url[len("/media/") :].lstrip("/")
    if url.startswith("s3://"):
        rest = url[len("s3://") :]
        _, _, key = rest.partition("/")
        return key or None
    parsed = urlsplit(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        bucket = settings.vultr_s3_bucket
        path = parsed.path.lstrip("/")
        if path.startswith(f"{bucket}/"):
            return path[len(bucket) + 1 :]
        return path or None
    # nothing URL-y. maybe it's already a key, or a local path we can
    # relativise.
    if not url.startswith("/") and "://" not in url:
        return url
    try:
        return _key_from_path(Path(url))
    except Exception:
        return None


# ── url minting ────────────────────────────────────────────────────


def url_for_key(key: str) -> str:
    """External URL the client will load."""
    if not settings.s3_enabled:
        return f"/media/{key}"
    if settings.media_url_mode == "public":
        host = urlparse(settings.vultr_s3_endpoint).netloc
        return f"https://{host}/{settings.vultr_s3_bucket}/{key}"
    # presigned GET
    c = _client()
    assert c is not None
    return c.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.vultr_s3_bucket, "Key": key},
        ExpiresIn=settings.presign_expiry,
    )


def url_for_path(p: Path) -> str:
    return url_for_key(_key_from_path(p))


def download_url_for_key(key: str, *, filename: str | None = None) -> str:
    """Presigned URL that forces the browser to save the file instead of
    navigating to it inline.

    The trick: include ResponseContentDisposition=attachment in the
    signed GET params. S3 (and S3-compat gateways like Vultr Object
    Storage) will echo that as a `Content-Disposition: attachment` header
    on the response, which any browser interprets as "trigger a file
    download" regardless of Content-Type. This lets the frontend use a
    plain `<a href=... download>` (no fetch, no CORS pre-flight) and still
    end up with a saved .mp4 rather than a new tab full of <video>.

    Falls back to the plain URL when S3 isn't enabled or presigning
    doesn't apply (public/local modes).
    """
    if not settings.s3_enabled or settings.media_url_mode == "public":
        return url_for_key(key)
    c = _client()
    assert c is not None
    disp = "attachment"
    if filename:
        # filename has already been sanitized to ascii-safe chars at the
        # caller; still keep it quoted to handle any spaces.
        disp = f'attachment; filename="{filename}"'
    return c.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.vultr_s3_bucket,
            "Key": key,
            "ResponseContentDisposition": disp,
        },
        ExpiresIn=settings.presign_expiry,
    )


# ── scratch-path helpers (local) ────────────────────────────────────


def path_for(category: str, filename: str) -> Path:
    p = settings.storage_path / category / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def new_path(category: str, ext: str) -> tuple[Path, str]:
    """Reserve a new (local scratch path, external url) pair.

    The URL is already "valid" in S3 mode (it's a presigned GET URL for a
    key that *will* exist after you call publish()). Caller contract:
      1. write the file at `path`
      2. `await publish(path)` to upload to S3 (noop in local mode)
    """
    name = _new_name(ext)
    p = path_for(category, name)
    return p, url_for_key(f"{category}/{name}")


def public_url(category: str, filename: str) -> str:
    """Back-compat: url for a category+filename pair (no new upload)."""
    return url_for_key(f"{category}/{filename}")


# ── s3 io (async wrappers around boto3) ─────────────────────────────


async def publish(path: Path, *, content_type: Optional[str] = None) -> str:
    """Upload a local scratch file to Vultr. Returns the external URL.

    In local (non-S3) mode this is a no-op that just returns the /media URL.
    """
    if not settings.s3_enabled:
        return url_for_path(path)
    c = _client()
    assert c is not None
    key = _key_from_path(path)
    extra = {"ContentType": content_type} if content_type else {}
    await asyncio.to_thread(
        c.upload_file,
        str(path),
        settings.vultr_s3_bucket,
        key,
        ExtraArgs=extra or None,
    )
    log.debug("published %s -> s3://%s/%s", path, settings.vultr_s3_bucket, key)
    return url_for_key(key)


async def _download_to(key: str, dest: Path) -> None:
    c = _client()
    assert c is not None
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(c.download_file, settings.vultr_s3_bucket, key, str(dest))


async def path_from_url(url: str) -> Path:
    """Return a local path for an existing S3 object, downloading if missing.

    Signature used to be sync; it's now async because we may hit the
    network. Callers that pass a raw local path string still get back a
    plain Path with no roundtrip.
    """
    key = _key_from_url(url)
    if key is None:
        return Path(url)
    local = settings.storage_path / key
    if local.exists():
        return local
    if not settings.s3_enabled:
        # nothing to download from. honour the historic behaviour: hand
        # back the path even if it doesn't exist (caller will 404 itself).
        return local
    try:
        await _download_to(key, local)
    except ClientError as e:
        log.warning("download miss for %s: %s", key, e)
    return local


# ── high-level: upload / write ──────────────────────────────────────


_EXT_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def _guess_content_type(path: Path, fallback: Optional[str] = None) -> Optional[str]:
    if fallback and fallback != "application/octet-stream":
        return fallback
    return _EXT_CONTENT_TYPES.get(path.suffix.lower()) or fallback


async def save_upload(upload: UploadFile, category: str = "uploads") -> tuple[Path, str]:
    """Stream an UploadFile to scratch, then publish to S3.

    Returns (local_path, external_url). Local file is kept as a cache so
    the ffmpeg pipeline (probe, extract_clip, extract_frame) can run
    without re-downloading.
    """
    suffix = Path(upload.filename or "video.mp4").suffix or ".mp4"
    name = _new_name(suffix)
    dest = path_for(category, name)
    async with aiofiles.open(dest, "wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            await out.write(chunk)
    ct = _guess_content_type(dest, upload.content_type or None)
    url = await publish(dest, content_type=ct)
    return dest, url


async def write_bytes(category: str, ext: str, data: bytes) -> tuple[Path, str]:
    p, _ = new_path(category, ext)
    async with aiofiles.open(p, "wb") as f:
        await f.write(data)
    url = await publish(p)
    return p, url


# ── url helpers for workers that receive "whatever" url-likes ──────


def normalize_url_like(value: str, *, fallback: Optional[str] = None) -> str:
    """Accept a URL, an S3 key, or a local path string — return a *fresh*
    client URL.

    Critically, this always re-derives the S3 key and mints a new
    presigned URL when S3 is enabled, so previously-issued URLs that have
    since expired get refreshed on read (e.g. the Projects list view).
    """
    if not value:
        return fallback or ""
    # external URLs that clearly aren't ours: leave alone.
    if value.startswith(("http://", "https://")):
        key = _key_from_url(value)
        if key and settings.s3_enabled:
            try:
                return url_for_key(key)
            except Exception:
                return value
        return value
    if value.startswith("/media/") or value.startswith("s3://"):
        key = _key_from_url(value)
        return url_for_key(key) if key else value
    p = Path(value)
    try:
        return url_for_path(p)
    except Exception:
        return fallback or value
