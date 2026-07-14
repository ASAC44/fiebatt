"""Shared config for AI services.

Keeps the mode switch and provider credentials in one place so the stub/real
boundary stays explicit.
"""

from contextvars import ContextVar
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    use_ai_stubs: bool = Field(True, alias="USE_AI_STUBS")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    dashscope_api_key: str = Field("", alias="DASHSCOPE_API_KEY")
    mesh_api_key: str = Field("", alias="MESH_API_KEY")
    mesh_api_base_url: str = Field("https://api.meshapi.ai/v1", alias="MESH_API_BASE_URL")
    mesh_model: str = Field("deepseek/deepseek-v3.2", alias="MESH_MODEL")
    mesh_video_model: str = Field("google/veo-3", alias="MESH_VIDEO_MODEL")
    mesh_video_endpoint: str = Field("/videos/generations", alias="MESH_VIDEO_ENDPOINT")
    elevenlabs_api_key: str = Field("", alias="ELEVENLABS_API_KEY")
    storage_path: str = Field("./storage", alias="STORAGE_PATH")
    gpu_worker_url: str = Field("http://localhost:8001", alias="GPU_WORKER_URL")
    video_gen_provider: str = Field("wan", alias="VIDEO_GEN_PROVIDER")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def ai_mode(self) -> str:
        return "stub" if self.use_ai_stubs else "real"

    @property
    def normalized_video_gen_provider(self) -> str:
        provider = self.video_gen_provider.strip().lower()
        if provider == "veo":
            return "veo"
        if provider == "happyhorse":
            return "happyhorse"
        if provider in {"meshapi_veo", "mesh-veo", "mesh_veo"}:
            return "meshapi_veo"
        if provider == "wan":
            return "wan"
        return "wan"

    @property
    def video_gen_provider_label(self) -> str:
        provider = self.normalized_video_gen_provider
        if provider == "wan":
            return "Wan"
        if provider == "happyhorse":
            return "HappyHorse"
        if provider == "meshapi_veo":
            return "Mesh API Veo"
        return "Veo"

    @property
    def real_ai_ready(self) -> bool:
        return bool(
            self.mesh_api_key.strip()
            or self.dashscope_api_key.strip()
            or self.gemini_api_key.strip()
        )

    def require_real_ai(self, *, provider: str) -> None:
        if self.use_ai_stubs:
            raise RuntimeError(
                f"{provider} real provider requested while USE_AI_STUBS=true. "
                "set USE_AI_STUBS=false to use live ai providers."
            )
        if not self.real_ai_ready:
            raise RuntimeError(
                f"{provider} requires MESH_API_KEY, DASHSCOPE_API_KEY, or GEMINI_API_KEY when USE_AI_STUBS=false."
            )


@lru_cache
def _base_settings() -> Settings:
    return Settings()


_settings_overrides: ContextVar[dict[str, str]] = ContextVar(
    "fiebatt_ai_settings_overrides",
    default={},
)


def set_settings_overrides(values: dict[str, str]) -> None:
    """Apply request/job-scoped provider credentials to the current task."""
    _settings_overrides.set(dict(values))


def clear_settings_overrides() -> None:
    _settings_overrides.set({})


def get_settings() -> Settings:
    base = _base_settings()
    overrides = _settings_overrides.get()
    return base.model_copy(update=overrides) if overrides else base
