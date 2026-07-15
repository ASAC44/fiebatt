"""Spike: test Gemini edit plan generation.

Run: cd /path/to/fiebatt && python scripts/spike_gemini.py
Requires: GEMINI_API_KEY in .env
"""

import asyncio
import json
import sys
import os

# Add the backend package root to the import path.
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(repo_root, "apps", "backend"))

from dotenv import load_dotenv
load_dotenv()

from app.ai.services.qwen import create_edit_plan, generate_narration_script


async def main():
    print("=== SPIKE: Gemini Edit Plan ===\n")

    print("sending prompt: 'make this car red'")
    print("bbox: center-left region (x=0.25, y=0.40, w=0.30, h=0.35)")
    print("entity: silver sedan car\n")

    plan = await create_edit_plan(
        prompt="make this car red",
        bbox={"x": 0.25, "y": 0.40, "w": 0.30, "h": 0.35},
        entity_description="silver sedan car",
    )

    print("--- EDIT PLAN ---")
    print(json.dumps(plan, indent=2))

    print(f"\nvariants returned: {len(plan.get('variants', []))}")
    for i, v in enumerate(plan.get("variants", [])):
        print(f"\n  variant {chr(65+i)}:")
        print(f"    description: {v.get('description', 'N/A')}")
        print(f"    tone: {v.get('tone', 'N/A')}")
        print(f"    prompt_for_veo length: {len(v.get('prompt_for_veo', ''))}")

    # also test narration
    if plan.get("variants"):
        print("\n=== SPIKE: Narration Script ===\n")
        desc = plan["variants"][0].get("description", "red car transformation")
        script = await generate_narration_script(
            variant_description=desc,
            original_prompt="make this car red",
        )
        print(f"narration: {script}")

    print("\n=== SPIKE COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(main())
