"""Vision-language service — powered by an OpenAI-compatible model gateway.

Handles 5 distinct vision+language tasks:
1. Prompt structuring (raw prompt -> structured edit plan)
2. Entity identification (bbox crop -> entity description)
3. Entity search (keyframe batch -> which frames contain the entity)
4. Quality scoring (generated clip frames -> visual_coherence + prompt_adherence)
5. Narration script generation (variant description -> cinematic voiceover text)

By default this uses ``qwen3.7-plus`` via DashScope. When ``MESH_API_KEY`` is
configured it routes through Mesh API using ``MESH_MODEL`` instead.
"""

import base64
import json
from pathlib import Path

from openai import AsyncOpenAI

from app.ai.services.config import get_settings
from app.ai.services.logger import tracked

QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
QWEN_REQUEST_TIMEOUT_SECONDS = 60.0

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


def _provider_config(*, needs_vision: bool = False) -> tuple[str, str, str]:
    settings = get_settings()
    api_key = settings.dashscope_api_key or settings.gemini_api_key
    if needs_vision and api_key:
        return api_key, QWEN_BASE_URL, DEFAULT_MODEL
    if settings.mesh_api_key:
        return settings.mesh_api_key, settings.mesh_api_base_url, settings.mesh_model
    if not api_key:
        raise RuntimeError(
            "MESH_API_KEY, DASHSCOPE_API_KEY, or GEMINI_API_KEY required for vision-language calls"
        )
    return api_key, QWEN_BASE_URL, DEFAULT_MODEL


def _make_client(*, needs_vision: bool = False) -> AsyncOpenAI:
    api_key, base_url, _model = _provider_config(needs_vision=needs_vision)
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=QWEN_REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )


def _request_extras(base_url: str) -> dict:
    # Qwen 3.7 enables deep thinking by default. These calls perform bounded
    # planning and tool work, where thinking adds minutes of latency without
    # improving the required structured response.
    return {"enable_thinking": False} if base_url == QWEN_BASE_URL else {}


def _content_has_image(content: str | list[dict]) -> bool:
    if isinstance(content, str):
        return False
    return any("image_url" in item for item in content)


async def _chat_json(
    system_prompt: str,
    user_content: list[dict],
    model: str = DEFAULT_MODEL,
) -> str:
    """Send a chat request with JSON response format and return the raw text."""
    needs_vision = _content_has_image(user_content)
    client = _make_client(needs_vision=needs_vision)
    _api_key, base_url, configured_model = _provider_config(needs_vision=needs_vision)
    json_system_prompt = (
        f"{system_prompt.rstrip()}\n\n"
        "Return only valid JSON matching the requested schema."
    )
    resp = await client.chat.completions.create(
        model=configured_model if model == DEFAULT_MODEL else model,
        messages=[
            {"role": "system", "content": json_system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        extra_body=_request_extras(base_url),
    )
    return resp.choices[0].message.content or ""


async def _chat_text(
    system_prompt: str,
    user_content: str | list[dict],
    model: str = DEFAULT_MODEL,
) -> str:
    """Send a chat request returning free-form text."""
    needs_vision = _content_has_image(user_content)
    client = _make_client(needs_vision=needs_vision)
    _api_key, base_url, configured_model = _provider_config(needs_vision=needs_vision)
    msg = (
        {"role": "user", "content": user_content}
        if isinstance(user_content, str)
        else {"role": "user", "content": user_content}
    )
    resp = await client.chat.completions.create(
        model=configured_model if model == DEFAULT_MODEL else model,
        messages=[{"role": "system", "content": system_prompt}, msg],
        temperature=0.3,
        extra_body=_request_extras(base_url),
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
    *,
    target_frame_paths: list[str] | None = None,
    reference_target_path: str | None = None,
) -> dict[str, int | list[str]]:
    """Score a generated variant on visual coherence and prompt adherence.

    Returns:
        Scores plus concrete evidence for any failure.
    """
    system_prompt = _load_prompt("quality_score")
    content: list[dict] = [{
        "type": "text",
        "text": (
            "NON-NEGOTIABLE EDIT REQUIREMENT:\n"
            f"{original_prompt}\n\n"
            "Judge exact attributes, not general similarity. Full frames show spill "
            "and scene preservation. Enlarged target crops show whether the selected "
            "change is correct."
        ),
    }]
    if reference_target_path:
        content.extend([
            {"type": "text", "text": "SOURCE TARGET BEFORE EDIT:"},
            {"type": "image_url", "image_url": {"url": _image_data_url(reference_target_path)}},
        ])
    for index, path in enumerate(variant_frame_paths):
        content.append({"type": "text", "text": f"GENERATED FULL FRAME {index + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
    for index, path in enumerate(target_frame_paths or []):
        content.extend([
            {"type": "text", "text": f"GENERATED TARGET CROP {index + 1}:"},
            {"type": "image_url", "image_url": {"url": _image_data_url(path)}},
        ])

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
