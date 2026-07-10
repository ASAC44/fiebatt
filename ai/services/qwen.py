"""Vision-language service — powered by Qwen (DashScope OpenAI-compatible API).

Handles 5 distinct vision+language tasks:
1. Prompt structuring (raw prompt -> structured edit plan)
2. Entity identification (bbox crop -> entity description)
3. Entity search (keyframe batch -> which frames contain the entity)
4. Quality scoring (generated clip frames -> visual_coherence + prompt_adherence)
5. Narration script generation (variant description -> cinematic voiceover text)

All use ``qwen3.7-plus`` via the OpenAI-compatible DashScope endpoint.
"""

import base64
import json
from pathlib import Path

from openai import AsyncOpenAI

from ai.services.config import get_settings
from ai.services.logger import tracked

QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text()


def _image_data_url(path: str) -> str:
    """Convert a local image path to a data URL for the OpenAI content array."""
    p = Path(path)
    ext = p.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _make_client() -> AsyncOpenAI:
    settings = get_settings()
    api_key = settings.dashscope_api_key or settings.gemini_api_key
    if not api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY or GEMINI_API_KEY required for vision-language calls"
        )
    return AsyncOpenAI(api_key=api_key, base_url=QWEN_BASE_URL)


async def _chat_json(
    system_prompt: str,
    user_content: list[dict],
    model: str = DEFAULT_MODEL,
) -> str:
    """Send a chat request with JSON response format and return the raw text."""
    client = _make_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


async def _chat_text(
    system_prompt: str,
    user_content: str | list[dict],
    model: str = DEFAULT_MODEL,
) -> str:
    """Send a chat request returning free-form text."""
    client = _make_client()
    msg = (
        {"role": "user", "content": user_content}
        if isinstance(user_content, str)
        else {"role": "user", "content": user_content}
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}, msg],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


@tracked("gemini", "edit_plan")
async def create_edit_plan(
    prompt: str,
    bbox: dict[str, float],
    entity_description: str | None = None,
    frame_path: str | None = None,
) -> dict:
    """Convert a raw user prompt into a structured edit plan.

    Ships the reference frame to Qwen (vision) so the plan is grounded in
    what's actually on screen.
    """
    system_prompt = _load_prompt("edit_plan")
    user_payload = {
        "user_prompt": prompt,
        "bbox": bbox,
        "entity_description": entity_description,
        "bbox_is_full_frame": (
            bbox.get("w", 0) >= 0.98 and bbox.get("h", 0) >= 0.98
        ),
    }

    content: list[dict] = [{"type": "text", "text": json.dumps(user_payload)}]
    if frame_path:
        fp = Path(frame_path)
        if fp.exists():
            content.append({"type": "image_url", "image_url": {"url": _image_data_url(frame_path)}})

    text = await _chat_json(system_prompt, content)
    return json.loads(text)


@tracked("gemini", "identify_entity")
async def identify_entity(
    reference_crop_path: str,
) -> dict[str, str]:
    """Identify what an entity is from a cropped reference frame.

    Returns:
        {"description": str, "category": str, "visual_attributes": str}
    """
    system_prompt = _load_prompt("entity_identify")
    content: list[dict] = [
        {"type": "text", "text": "Identify the entity in this image."},
        {"type": "image_url", "image_url": {"url": _image_data_url(reference_crop_path)}},
    ]
    text = await _chat_json(system_prompt, content)
    return json.loads(text)


@tracked("gemini", "search_keyframes")
async def search_keyframes_for_entity(
    entity_description: str,
    keyframe_paths: list[str],
) -> list[dict]:
    """Search a batch of keyframes for an entity (max 10 per call).

    Returns:
        List of {"keyframe_index": int, "confidence": float, "found": bool}
    """
    system_prompt = _load_prompt("entity_search")
    content: list[dict] = [
        {"type": "text", "text": f"Find this entity in the following keyframes: {entity_description}"},
    ]
    for path in keyframe_paths[:10]:
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})

    text = await _chat_json(system_prompt, content)
    return json.loads(text)


@tracked("gemini", "score_variant")
async def score_variant(
    original_prompt: str,
    variant_frame_paths: list[str],
) -> dict[str, int]:
    """Score a generated variant on visual coherence and prompt adherence.

    Returns:
        {"visual_coherence": int (1-10), "prompt_adherence": int (1-10)}
    """
    system_prompt = _load_prompt("quality_score")
    content: list[dict] = [
        {"type": "text", "text": f"Original prompt: {original_prompt}"},
    ]
    for path in variant_frame_paths:
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})

    text = await _chat_json(system_prompt, content)
    return json.loads(text)


@tracked("gemini", "narration_script")
async def generate_narration_script(
    variant_description: str,
    original_prompt: str,
) -> str:
    """Generate a short cinematic narration script for the before/after reveal."""
    system_prompt = _load_prompt("narration")
    user_text = f"Variant: {variant_description}\nOriginal prompt: {original_prompt}"
    return await _chat_text(system_prompt, user_text)
