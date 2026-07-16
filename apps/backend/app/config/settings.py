from pathlib import Path
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # database url. defaults to local sqlite so the app boots without postgres.
    # for real deploys set DATABASE_URL=postgresql+asyncpg://fiebatt:fiebatt@host:5432/fiebatt
    database_url: str = "sqlite+aiosqlite:///./fiebatt.db"

    # local scratch dir — ffmpeg needs real file paths, so we write here first
    # and upload to vultr on publish(). once s3 is wired this is just a cache,
    # never user-facing.
    storage_path: Path = Path("./storage")

    gemini_api_key: str = ""
    dashscope_api_key: str = ""
    mesh_api_key: str = ""
    mesh_api_base_url: str = "https://api.meshapi.ai/v1"
    mesh_model: str = "deepseek/deepseek-v3.2"
    # kept for older env shapes; the current real video provider path uses
    # Gemini/Veo under the `runway.generate(...)` adapter surface.
    runway_api_key: str = ""
    elevenlabs_api_key: str = ""

    auth_jwt_secret: str = "change-me"
    auth_jwt_expires_minutes: int = 7 * 24 * 60
    oauth_access_token_minutes: int = 60
    oauth_refresh_token_days: int = 30
    public_api_url: str = "http://localhost:8000"
    app_url: str = "http://localhost:3001"
    credential_encryption_key: str = ""
    upload_intent_expiry_seconds: int = 15 * 60
    max_upload_bytes: int = 500 * 1024 * 1024

    max_video_seconds: int = 120

    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3001",
    ]

    # when true, worker calls resolve to ai/services/_stubs.py
    use_ai_stubs: bool = True
    # Plans can be previewed before PR2 changes generation semantics.
    adaptive_edit_planning: bool = False
    # PR3 global discovery/chunk orchestration. Local and legacy paths stay
    # unchanged until this independent rollout switch is enabled.
    global_edit_planning: bool = False
    global_edit_max_occurrences: int = 12
    global_edit_max_generation_calls: int = 24
    global_edit_max_generated_seconds: float = 180.0
    # Emergency operator escape hatch; request must also explicitly opt in.
    allow_hard_failed_acceptance: bool = False

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

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> str:
        url = str(value or "").strip()
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        return url

    @property
    def ai_mode(self) -> str:
        return "stub" if self.use_ai_stubs else "real"

    @property
    def real_ai_ready(self) -> bool:
        return bool(
            self.mesh_api_key.strip()
            or self.dashscope_api_key.strip()
            or self.gemini_api_key.strip()
        )

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

    @property
    def oauth_issuer(self) -> str:
        return self.public_api_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # scratch dirs — always exist locally even in s3 mode, since ffmpeg
    # writes here first.
    s.storage_path.mkdir(parents=True, exist_ok=True)
    for sub in ("uploads", "clips", "variants", "stitched", "exports", "narration", "keyframes"):
        (s.storage_path / sub).mkdir(parents=True, exist_ok=True)
    return s
