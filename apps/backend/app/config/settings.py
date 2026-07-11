from pathlib import Path
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # database url. defaults to local sqlite so the app boots without postgres.
    # for real deploys set DATABASE_URL=postgresql+asyncpg://iris:iris@host:5432/iris
    database_url: str = "sqlite+aiosqlite:///./iris.db"

    # local scratch dir — ffmpeg needs real file paths, so we write here first
    # and upload to vultr on publish(). once s3 is wired this is just a cache,
    # never user-facing.
    storage_path: Path = Path("./storage")

    gemini_api_key: str = ""
    dashscope_api_key: str = ""
    # kept for older env shapes; the current real video provider path uses
    # Gemini/Veo under the `runway.generate(...)` adapter surface.
    runway_api_key: str = ""
    elevenlabs_api_key: str = ""

    # legacy supabase jwt signing secret (HS256). still used by older
    # projects and required for the fast path when alg=HS256. when unset
    # the app falls back to anon session cookies (offline dev).
    supabase_jwt_secret: str = ""
    # supabase project URL, e.g. "https://abc123.supabase.co". used to
    # fetch JWKS (RS256/ES256 public keys) for modern supabase projects
    # that signed their tokens asymmetrically. if unset, only HS256 tokens
    # can be verified.
    supabase_url: str = ""

    max_video_seconds: int = 120

    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]

    # when true, worker calls resolve to ai/services/_stubs.py
    use_ai_stubs: bool = True

    # ── vultr object storage ────────────────────────────────────────
    # S3-compatible. empty values disable the integration and storage
    # falls back to the local scratch dir (behaves like the old /media
    # setup — handy for offline dev).
    vultr_s3_endpoint: str = ""
    vultr_s3_region: str = "ewr1"
    vultr_s3_bucket: str = ""
    vultr_s3_access_key: str = ""
    vultr_s3_secret_key: str = ""
    # "presigned" → bucket stays private, backend mints GET urls
    # "public"    → urls are https://{bucket}.{host}/{key} (requires
    #               bucket read policy set to public-read)
    media_url_mode: str = "presigned"
    presign_expiry: int = 7 * 24 * 3600  # 7 days, max for sigv4

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("media_url_mode", mode="before")
    @classmethod
    def _normalize_media_url_mode(cls, value: object) -> str:
        normalized = str(value or "presigned").strip().lower()
        if normalized not in {"presigned", "public"}:
            raise ValueError("MEDIA_URL_MODE must be 'presigned' or 'public'")
        return normalized

    @property
    def ai_mode(self) -> str:
        return "stub" if self.use_ai_stubs else "real"

    @property
    def real_ai_ready(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @property
    def narration_ai_ready(self) -> bool:
        return self.real_ai_ready and bool(self.elevenlabs_api_key.strip())

    @property
    def s3_enabled(self) -> bool:
        return bool(
            self.vultr_s3_endpoint
            and self.vultr_s3_bucket
            and self.vultr_s3_access_key
            and self.vultr_s3_secret_key
        )


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # scratch dirs — always exist locally even in s3 mode, since ffmpeg
    # writes here first.
    s.storage_path.mkdir(parents=True, exist_ok=True)
    for sub in ("uploads", "clips", "variants", "stitched", "exports", "narration", "keyframes"):
        (s.storage_path / sub).mkdir(parents=True, exist_ok=True)
    return s
