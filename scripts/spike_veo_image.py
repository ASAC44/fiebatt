"""Spike: test Veo image-conditioned generation.

The key question: can we pass a reference frame to guide video generation?
This is how bbox spatial grounding works — crop the bbox region, use it as
the starting frame for Veo.

Run: python scripts/spike_veo_image.py
"""

import asyncio
import time
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types


async def main():
    print("=== SPIKE: Veo Image-Conditioned Generation ===\n")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # generate a reference frame
    os.makedirs("storage/spike", exist_ok=True)
    frame_path = "storage/spike/reference_frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "color=c=0x333333:s=640x360:d=1",
         "-frames:v", "1", frame_path],
        capture_output=True, check=True,
    )

    # Method 1: upload file to genai first, then reference it
    print("uploading reference frame...")
    uploaded = client.files.upload(file=frame_path)
    print(f"uploaded: {uploaded.name}")

    # try passing as image via the uploaded file
    print("\ngenerating video with image conditioning...")
    print("prompt: 'This dark scene gradually fills with warm golden sunset light, cinematic'")

    start = time.monotonic()

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt="This dark scene gradually fills with warm golden sunset light streaming in from the right, cinematic, beautiful",
        image=types.Image(
            image_bytes=open(frame_path, "rb").read(),
            mime_type="image/png",
        ),
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

    print("\nRESULT: PASS")
    print("open storage/spike/test2_image_conditioned.mp4 to verify the reference frame influenced the output")


if __name__ == "__main__":
    asyncio.run(main())
