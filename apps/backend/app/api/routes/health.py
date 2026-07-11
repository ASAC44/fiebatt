from fastapi import APIRouter

from app.config.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    settings = get_settings()
    return {
        "ok": True,
        "ai_mode": settings.ai_mode,
        "real_ai_ready": settings.real_ai_ready,
        "narration_ai_ready": settings.narration_ai_ready,
        "storage_mode": "s3" if settings.s3_enabled else "local",
    }
