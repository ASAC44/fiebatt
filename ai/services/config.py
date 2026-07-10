"""Shared config for AI services.

Keeps the mode switch and provider credentials in one place so the stub/real
boundary stays explicit.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    use_ai_stubs: bool = Field(True, alias="USE_AI_STUBS")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    dashscope_api_key: str = Field("", alias="DASHSCOPE_API_KEY")
    elevenlabs_api_key: str = Field("", alias="ELEVENLABS_API_KEY")
    storage_path: str = Field("./storage", alias="STORAGE_PATH")
    gpu_worker_url: str = Field("http://localhost:8001", alias="GPU_WORKER_URL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def ai_mode(self) -> str:
        return "stub" if self.use_ai_stubs else "real"

    @property
    def real_ai_ready(self) -> bool:
        return bool(self.dashscope_api_key.strip() or self.gemini_api_key.strip())

    def require_real_ai(self, *, provider: str) -> None:
        if self.use_ai_stubs:
            raise RuntimeError(
                f"{provider} real provider requested while USE_AI_STUBS=true. "
                "set USE_AI_STUBS=false to use live ai providers."
            )
        if not self.real_ai_ready:
            raise RuntimeError(
                f"{provider} requires DASHSCOPE_API_KEY or GEMINI_API_KEY when USE_AI_STUBS=false."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
