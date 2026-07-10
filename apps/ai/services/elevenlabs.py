"""ElevenLabs service — voice narration for before/after reveals.

Generates cinematic voiceover audio from narration scripts produced by Gemini.
"""

from pathlib import Path

import httpx

from ai.services.config import get_settings
from ai.services.logger import tracked

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
# Rachel — clear, cinematic narrator voice
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


@tracked("elevenlabs", "narrate")
async def generate_narration(
    script: str,
    voice_id: str = DEFAULT_VOICE_ID,
) -> str:
    """Generate a voiceover audio clip from a narration script.

    Args:
        script: The narration text (1-2 sentences, from Gemini)
        voice_id: ElevenLabs voice ID

    Returns:
        Path to the generated audio file (mp3)
    """
    settings = get_settings()
    if not settings.elevenlabs_api_key.strip():
        raise RuntimeError(
            "elevenlabs narration requires ELEVENLABS_API_KEY."
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": settings.elevenlabs_api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": script,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.75,
                    "similarity_boost": 0.75,
                },
            },
            timeout=30.0,
        )
        response.raise_for_status()

    output_path = Path(settings.storage_path) / "narration" / f"narration_{hash(script)}.mp3"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)

    return str(output_path)
