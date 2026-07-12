"""Spike: test Veo 3.1 video generation through Gemini API.

Tests three things:
1. Basic text-to-video generation (does it work at all?)
2. Image-conditioned generation (can we use a reference frame?)
3. Spatial grounding via prompt (does bbox-informed prompt text affect the output?)

Run: python scripts/spike_veo.py
Requires: GEMINI_API_KEY in .env
"""

import asyncio
import time
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types


def get_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


async def test_text_to_video():
    """Test 1: basic text-to-video. Does Veo work at all?"""
    print("\n=== TEST 1: Text-to-Video ===")
    print("prompt: 'A silver sedan driving down a sunny city street, cinematic, 4K'")

    client = get_client()
    start = time.monotonic()

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt="A silver sedan driving down a sunny city street, cinematic, 4K",
        config=types.GenerateVideosConfig(
            aspect_ratio="16:9",
            duration_seconds="4",
            number_of_videos=1,
        ),
    )

    print("job submitted, polling...")
    while not operation.done:
        elapsed = time.monotonic() - start
        print(f"  waiting... ({elapsed:.0f}s)")
        await asyncio.sleep(10)
        operation = client.operations.get(operation)

    elapsed = time.monotonic() - start
    print(f"generation complete in {elapsed:.1f}s")

    video = operation.response.generated_videos[0]
    client.files.download(file=video.video)

    os.makedirs("storage/spike", exist_ok=True)
    video.video.save("storage/spike/test1_text_to_video.mp4")
    size = os.path.getsize("storage/spike/test1_text_to_video.mp4")
    print(f"saved: storage/spike/test1_text_to_video.mp4 ({size / 1024:.0f} KB)")
    print("RESULT: PASS")
    return True


async def test_image_conditioned():
    """Test 2: image-conditioned generation. Can we guide output with a reference frame?"""
    print("\n=== TEST 2: Image-Conditioned Generation ===")

    # generate a test frame first
    os.makedirs("storage/spike", exist_ok=True)
    frame_path = "storage/spike/reference_frame.png"

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "color=c=0x333333:s=640x360:d=1,drawtext=text='FIEBATT TEST':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
            "-frames:v", "1", frame_path,
        ],
        capture_output=True,
    )

    if not os.path.exists(frame_path):
        # fallback: simple solid frame
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x333333:s=640x360:d=1",
             "-frames:v", "1", frame_path],
            capture_output=True, check=True,
        )

    print(f"reference frame: {frame_path}")
    print("prompt: 'The scene transforms into a vibrant sunset, warm orange and pink tones flood the frame'")

    client = get_client()
    start = time.monotonic()

    ref_image = types.Image.from_file(frame_path)

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt="The scene transforms into a vibrant sunset, warm orange and pink tones flood the frame, cinematic lighting",
        image=ref_image,
        config=types.GenerateVideosConfig(
            aspect_ratio="16:9",
            duration_seconds="4",
            number_of_videos=1,
        ),
    )

    print("job submitted, polling...")
    while not operation.done:
        elapsed = time.monotonic() - start
        print(f"  waiting... ({elapsed:.0f}s)")
        await asyncio.sleep(10)
        operation = client.operations.get(operation)

    elapsed = time.monotonic() - start
    print(f"generation complete in {elapsed:.1f}s")

    video = operation.response.generated_videos[0]
    client.files.download(file=video.video)
    video.video.save("storage/spike/test2_image_conditioned.mp4")
    size = os.path.getsize("storage/spike/test2_image_conditioned.mp4")
    print(f"saved: storage/spike/test2_image_conditioned.mp4 ({size / 1024:.0f} KB)")
    print("RESULT: PASS")
    return True


async def test_spatial_grounding():
    """Test 3: spatial grounding via prompt text.

    This is the key question: if we describe WHERE in the frame the change should
    happen, does Veo respect that? We can't send bbox coords directly, but we can
    describe the region in the prompt (which is what Gemini's edit plan does).
    """
    print("\n=== TEST 3: Spatial Grounding via Prompt ===")
    print("testing if Veo respects spatial language in prompts...")
    print("prompt A (left): 'On the left side of the frame, a red sports car. Right side is empty road.'")
    print("prompt B (right): 'Left side is empty road. On the right side of the frame, a red sports car.'")

    client = get_client()
    results = {}

    for label, prompt in [
        ("left", "On the left side of the frame, a bright red sports car drives forward. The right side of the frame is empty open road. Cinematic, 4K."),
        ("right", "The left side of the frame is empty open road. On the right side of the frame, a bright red sports car drives forward. Cinematic, 4K."),
    ]:
        print(f"\n  generating '{label}' variant...")
        start = time.monotonic()

        operation = client.models.generate_videos(
            model="veo-3.1-generate-preview",
            prompt=prompt,
            config=types.GenerateVideosConfig(
                aspect_ratio="16:9",
                duration_seconds="4",
                number_of_videos=1,
            ),
        )

        while not operation.done:
            elapsed = time.monotonic() - start
            print(f"    waiting... ({elapsed:.0f}s)")
            await asyncio.sleep(10)
            operation = client.operations.get(operation)

        elapsed = time.monotonic() - start
        print(f"  done in {elapsed:.1f}s")

        video = operation.response.generated_videos[0]
        client.files.download(file=video.video)
        path = f"storage/spike/test3_spatial_{label}.mp4"
        video.video.save(path)
        size = os.path.getsize(path)
        print(f"  saved: {path} ({size / 1024:.0f} KB)")
        results[label] = path

    print("\n  MANUAL CHECK REQUIRED:")
    print(f"  open {results['left']} — car should be on the LEFT")
    print(f"  open {results['right']} — car should be on the RIGHT")
    print("  if the car position differs between the two, spatial grounding works.")
    print("RESULT: CHECK FILES MANUALLY")
    return True


async def main():
    print("=== VEO 3.1 SPIKE ===")
    print(f"API key: {'set' if os.environ.get('GEMINI_API_KEY') else 'MISSING'}")

    passed = 0
    failed = 0

    for test_fn in [test_text_to_video, test_image_conditioned, test_spatial_grounding]:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"\nFAILED: {e}")
            failed += 1

    print(f"\n=== SPIKE RESULTS: {passed} passed, {failed} failed ===")
    print("check storage/spike/ for generated videos")


if __name__ == "__main__":
    asyncio.run(main())
