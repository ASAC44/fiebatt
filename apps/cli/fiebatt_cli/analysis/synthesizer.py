"""Merge all chunk reports into a unified video understanding."""

import json
import logging

from google import genai

logger = logging.getLogger(__name__)

SYNTHESIS_PROMPT = """You are analyzing a video that has been broken into chunks. Below are the analysis results from each chunk, ordered by timestamp.

Synthesize these into a single comprehensive analysis of the entire video.

Return a JSON object:
{
  "overall_description": "A 15-second clip of a downtown street scene transitioning from day to dusk",
  "duration": 15.0,
  "scenes": [
    {"start_ts": 0.0, "end_ts": 5.0, "description": "Wide shot of intersection with light traffic"}
  ],
  "entities": [
    {
      "name": "red sedan",
      "category": "vehicle",
      "appearances": [
        {"start_ts": 2.0, "end_ts": 8.0, "bbox_hint": {"x": 0.2, "y": 0.4, "w": 0.3, "h": 0.2}}
      ]
    }
  ],
  "mood_arc": [
    {"ts": 0.0, "mood": "calm"},
    {"ts": 10.0, "mood": "tense"}
  ],
  "lighting_conditions": "Transitions from golden hour to dusk",
  "suggested_edits": [
    {
      "start_ts": 2.0,
      "end_ts": 4.0,
      "bbox_hint": {"x": 0.2, "y": 0.4, "w": 0.3, "h": 0.2},
      "suggestion": "Change the red sedan to blue",
      "rationale": "The red sedan is a prominent, trackable object ideal for demonstrating color change"
    }
  ]
}

Respond with ONLY the JSON object."""


async def synthesize(
    chunk_reports: list[dict],
    project_id: str,
    duration: float,
    api_key: str,
    model: str = "gemini-2.5-pro",
) -> dict:
    """Synthesize chunk reports into a unified video analysis.

    Args:
        chunk_reports: ordered list of chunk analysis dicts
        project_id: the project being analyzed
        duration: total video duration in seconds
        api_key: Gemini API key
        model: model for synthesis (use pro for quality)

    Returns:
        Unified analysis dict with project_id, duration, and raw_chunks attached
    """
    client = genai.Client(api_key=api_key)

    chunks_text = "\n\n".join(
        f"--- Chunk {i + 1} (frames {report.get('frames_analyzed', [])}) ---\n"
        f"{json.dumps(report, indent=2)}"
        for i, report in enumerate(chunk_reports)
    )

    prompt = f"""Video duration: {duration:.1f} seconds
Project ID: {project_id}

{SYNTHESIS_PROMPT}

Chunk analyses:
{chunks_text}"""

    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result: dict = json.loads(text)
    result["project_id"] = project_id
    result["duration"] = duration
    result["raw_chunks"] = chunk_reports
    return result
