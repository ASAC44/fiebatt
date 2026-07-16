"""Hugging Face ZeroGPU endpoint for Fiebatt's SAM2 segmentation worker."""

from __future__ import annotations

import base64
import binascii
import io
from typing import Any

import gradio as gr
import numpy as np
import spaces
import torch
from huggingface_hub import hf_hub_download
from PIL import Image, UnidentifiedImageError
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

MODEL_REPO = "facebook/sam2.1-hiera-small"
CHECKPOINT_NAME = "sam2.1_hiera_small.pt"
SAM_CONFIG = "configs/sam2.1/sam2.1_hiera_s.yaml"
MAX_IMAGE_PIXELS = 32_000_000

checkpoint_path = hf_hub_download(MODEL_REPO, CHECKPOINT_NAME)
predictor: SAM2ImagePredictor | None = None


def _get_predictor() -> SAM2ImagePredictor:
    """Build SAM2 only while ZeroGPU has attached a real CUDA device.

    SAM2's Hydra constructor deep-copies attention layers and is incompatible
    with ZeroGPU's emulated CUDA during module import. The initialized
    predictor remains cached in the GPU worker for subsequent requests.
    """
    global predictor
    if predictor is None:
        model = build_sam2(SAM_CONFIG, checkpoint_path, device="cuda")
        model.eval()
        predictor = SAM2ImagePredictor(model)
    return predictor


def _decode_image(encoded: Any) -> Image.Image:
    if not isinstance(encoded, str) or not encoded.strip():
        raise gr.Error("image_b64 must be a non-empty base64 string")

    value = encoded.strip()
    if value.startswith("data:"):
        try:
            value = value.split(",", 1)[1]
        except IndexError as exc:
            raise gr.Error("image_b64 contains an invalid data URL") from exc

    try:
        raw = base64.b64decode(value, validate=True)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (binascii.Error, UnidentifiedImageError, OSError) as exc:
        raise gr.Error("image_b64 is not a valid encoded image") from exc

    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise gr.Error("image is too large; maximum is 32 megapixels")
    return image.convert("RGB")


def _pixel_box(bbox: Any, width: int, height: int) -> np.ndarray:
    if not isinstance(bbox, dict):
        raise gr.Error("bbox must be an object with x, y, w, and h")

    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        box_width = float(bbox["w"])
        box_height = float(bbox["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise gr.Error("bbox must contain numeric x, y, w, and h values") from exc

    values = np.asarray([x, y, box_width, box_height], dtype=np.float32)
    if not np.isfinite(values).all():
        raise gr.Error("bbox values must be finite")
    if box_width <= 0 or box_height <= 0:
        raise gr.Error("bbox width and height must be greater than zero")

    x1 = float(np.clip(x, 0.0, 1.0))
    y1 = float(np.clip(y, 0.0, 1.0))
    x2 = float(np.clip(x + box_width, 0.0, 1.0))
    y2 = float(np.clip(y + box_height, 0.0, 1.0))
    if x2 <= x1 or y2 <= y1:
        raise gr.Error("bbox does not overlap the image")

    return np.asarray(
        [x1 * width, y1 * height, x2 * width, y2 * height],
        dtype=np.float32,
    )


def _encode_mask(mask: np.ndarray) -> str:
    mask_image = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    buffer = io.BytesIO()
    mask_image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@spaces.GPU(duration=60)
def segment(data: dict[str, Any]) -> dict[str, Any]:
    """Refine a normalized bounding box into a SAM2 binary mask."""
    if not isinstance(data, dict):
        raise gr.Error("request must be a JSON object")

    image = _decode_image(data.get("image_b64"))
    box = _pixel_box(data.get("bbox"), image.width, image.height)
    sam_predictor = _get_predictor()

    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        sam_predictor.set_image(np.asarray(image))
        masks, scores, _ = sam_predictor.predict(box=box, multimask_output=True)

    best_index = int(np.argmax(scores))
    return {
        "mask_b64": _encode_mask(masks[best_index]),
        "score": float(scores[best_index]),
        "candidate_count": int(len(masks)),
    }


example_request = {
    "image_b64": "Paste a base64-encoded PNG or JPEG here",
    "bbox": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
}

with gr.Blocks(title="Fiebatt SAM2") as demo:
    gr.Markdown(
        "# Fiebatt SAM2\n"
        "Submit a base64 image and normalized bounding box. "
        "Applications should call the named `/segment` API endpoint."
    )
    request = gr.JSON(value=example_request, label="Segmentation request")
    response = gr.JSON(label="SAM2 response")
    run = gr.Button("Segment", variant="primary")
    run.click(
        segment,
        inputs=request,
        outputs=response,
        api_name="segment",
        concurrency_limit=1,
    )

demo.queue(default_concurrency_limit=1, max_size=32)


if __name__ == "__main__":
    demo.launch(show_error=True)
