"""Single chunk analysis via Gemini vision API."""

import json
import logging
from pathlib import Path

from google import genai

logger = logging.getLogger(__name__)

CHUNK_PROMPT = """Analyze these video frames extracted at regular intervals. For each group of frames, provide a structured analysis.

Return a JSON object with these fields:
{
  "frames_analyzed": [0.0, 1.0, 2.0],
  "scene_description": "A busy city street at dusk with pedestrians and vehicles",
  "objects": [
    {
      "name": "red sedan",
      "bbox_approx": {"x": 0.2, "y": 0.4, "w": 0.3, "h": 0.2},
      "persistent": true
    }
  ],
  "entities": [
    {
      "description": "person in blue jacket",
      "category": "person",
      "first_seen_ts": 2.0
    }
  ],
  "mood": "energetic",
  "lighting": "golden hour, warm tones",
  "camera_motion": "static|pan|zoom|tracking",
  "notable_changes": ["car enters frame at 3.0s", "lighting shifts darker at 5.0s"]
}

Respond with ONLY the JSON object, no markdown or explanation."""


async def analyze_chunk(
    frames: list[tuple[float, Path]],
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> dict:
    """Analyze a chunk of frames using Gemini vision.

    Args:
        frames: list of (timestamp, frame_path) tuples
        api_key: Gemini API key
        model: model to use

    Returns:
        Structured analysis dict

    Raises:
        json.JSONDecodeError: if Gemini response is not valid JSON
        google.genai errors: on API failures
    """
    client = genai.Client(api_key=api_key)

    # Build parts: text label + prompt, then images
    timestamps = [ts for ts, _ in frames]
    ts_label = f"Frames at timestamps: {', '.join(f'{t:.1f}s' for t in timestamps)}"

    parts: list = [ts_label + "\n\n" + CHUNK_PROMPT]

    for _ts, frame_path in frames:
        img_bytes = frame_path.read_bytes()
        parts.append(
            genai.types.Part.from_bytes(data=img_bytes, mime_type="image/png")
        )

    response = await client.aio.models.generate_content(
        model=model,
        contents=parts,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    text = response.text.strip()
    # Handle potential markdown code block wrapping
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result: dict = json.loads(text)
    # Ensure timestamps are set from our known values
    result["frames_analyzed"] = timestamps
    return result
