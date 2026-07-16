"""Vision worker for accelerated segmentation and image embeddings.

Serves SAM2 segmentation and CLIP embeddings over HTTP.
The main backend calls this service for GPU-accelerated tasks.
"""

import base64
import io
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import torch
import numpy as np
from PIL import Image
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from tracking_contract import bounded_window, stub_frame

app = FastAPI(title="fiebatt-vision-worker")

# ---------- lazy model loading ----------

_sam_model = None
_sam_video_model = None
_clip_model = None
_clip_preprocess = None


def get_sam():
    global _sam_model
    if _sam_model is None:
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        import sam2

        checkpoint = str(Path(__file__).parent / "checkpoints" / "sam2.1_hiera_small.pt")
        # hydra resolves config by name from sam2 package's search path
        config = "configs/sam2.1/sam2.1_hiera_s.yaml"
        device = get_device()
        _sam_model = SAM2ImagePredictor(build_sam2(config, checkpoint, device=device))
    return _sam_model


def get_sam_video():
    global _sam_video_model
    if _sam_video_model is None:
        from sam2.build_sam import build_sam2_video_predictor

        checkpoint = str(Path(__file__).parent / "checkpoints" / "sam2.1_hiera_small.pt")
        config = "configs/sam2.1/sam2.1_hiera_s.yaml"
        _sam_video_model = build_sam2_video_predictor(
            config, checkpoint, device=get_device()
        )
    return _sam_video_model


def get_clip():
    global _clip_model, _clip_preprocess
    if _clip_model is None:
        import open_clip

        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        _clip_model.eval()
        if torch.cuda.is_available():
            _clip_model = _clip_model.cuda()
        elif torch.backends.mps.is_available():
            _clip_model = _clip_model.to("mps")
    return _clip_model, _clip_preprocess


# ---------- helpers ----------

def b64_to_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def mask_to_b64(mask: np.ndarray) -> str:
    img = Image.fromarray((mask * 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class TrackRequest(BaseModel):
    frames_b64: list[str] = Field(min_length=1, max_length=240)
    seed_frame_index: int = Field(ge=0)
    bbox: dict[str, float] | None = None
    seed_mask_b64: str | None = None
    max_frames: int = Field(default=120, ge=1, le=240)
    max_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    lost_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    include_masks: bool = True


def _mask_box(mask: np.ndarray) -> dict[str, float] | None:
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    height, width = mask.shape
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    return {
        "x": x1 / width,
        "y": y1 / height,
        "w": (x2 - x1) / width,
        "h": (y2 - y1) / height,
    }


def _rectangle_mask(image: Image.Image, bbox: dict[str, float]) -> np.ndarray:
    width, height = image.size
    mask = np.zeros((height, width), dtype=bool)
    x1 = max(0, min(width, round(bbox["x"] * width)))
    y1 = max(0, min(height, round(bbox["y"] * height)))
    x2 = max(x1, min(width, round((bbox["x"] + bbox["w"]) * width)))
    y2 = max(y1, min(height, round((bbox["y"] + bbox["h"]) * height)))
    mask[y1:y2, x1:x2] = True
    return mask


def _stub_track(
    frames: list[Image.Image],
    *,
    bbox: dict[str, float],
    include_masks: bool,
    source_offset: int,
) -> list[dict[str, Any]]:
    """Deterministic CPU/dev fallback with the same response contract."""
    results = []
    for index, image in enumerate(frames):
        mask = _rectangle_mask(image, bbox)
        frame = stub_frame(source_offset + index, bbox)
        frame["bbox"] = _mask_box(mask)
        frame["mask_b64"] = mask_to_b64(mask) if include_masks else None
        results.append(frame)
    return results


# ---------- endpoints ----------

def get_device():
    if torch.cuda.is_available(): return "cuda"
    if torch.backends.mps.is_available(): return "mps"
    return "cpu"

@app.get("/health")
async def health():
    device = get_device()
    return {
        "status": "ok",
        "gpu": device != "cpu",
        "device": device,
    }


@app.post("/sam/segment")
async def sam_segment(data: dict):
    """Segment an entity from a frame using a bounding box prompt.

    Input: {"image_b64": str, "bbox": {"x": float, "y": float, "w": float, "h": float}}
    Output: {"mask_b64": str}
    """
    image = b64_to_image(data["image_b64"])
    bbox = data["bbox"]
    w, h = image.size

    # convert normalized bbox to pixel coords [x1, y1, x2, y2]
    box = np.array([
        bbox["x"] * w,
        bbox["y"] * h,
        (bbox["x"] + bbox["w"]) * w,
        (bbox["y"] + bbox["h"]) * h,
    ])

    predictor = get_sam()
    predictor.set_image(np.array(image))
    # A box can legitimately contain several plausible targets (for example a
    # person, their shadow, and the ground behind them).  Ask SAM for all three
    # candidates and keep the model's highest-confidence result instead of
    # accepting the single-mask shortcut unconditionally.
    masks, scores, _ = predictor.predict(box=box, multimask_output=True)
    best_index = int(np.argmax(scores))
    best_mask = masks[best_index]  # shape: (H, W), bool
    return {
        "mask_b64": mask_to_b64(best_mask),
        "score": float(scores[best_index]),
        "candidate_count": int(len(masks)),
    }


@app.post("/sam/track")
async def sam_track(data: TrackRequest, request: Request):
    """Track one selected object through a bounded, contiguous frame window."""
    if data.seed_frame_index >= len(data.frames_b64):
        raise HTTPException(status_code=422, detail="seed_frame_index outside frames")
    if data.bbox is None and data.seed_mask_b64 is None:
        raise HTTPException(status_code=422, detail="bbox or seed_mask_b64 is required")

    window_start, window_end, seed_index = bounded_window(
        len(data.frames_b64), data.seed_frame_index, data.max_frames
    )
    images = [b64_to_image(value) for value in data.frames_b64[window_start:window_end]]
    bbox = data.bbox

    allow_stub = os.getenv("VISION_TRACKER_ALLOW_STUB", "true").lower() in {
        "1", "true", "yes", "on"
    }
    try:
        predictor = get_sam_video()
    except Exception as exc:
        if not allow_stub or bbox is None:
            raise HTTPException(status_code=503, detail=f"SAM2 video tracker unavailable: {exc}")
        return {
            "tracker": "stub",
            "processed_start_index": window_start,
            "processed_end_index": window_end - 1,
            "cancelled": False,
            "frames": _stub_track(
                images,
                bbox=bbox,
                include_masks=data.include_masks,
                source_offset=window_start,
            ),
            "warning": "SAM2 video tracker unavailable; bbox fallback used",
        }

    started = time.monotonic()
    cancelled = False
    outputs: dict[int, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="fiebatt-track-") as temp_dir:
        video_dir = Path(temp_dir)
        for index, image in enumerate(images):
            image.save(video_dir / f"{index:06d}.jpg", quality=95)

        state = predictor.init_state(video_path=str(video_dir))
        if data.seed_mask_b64:
            seed_mask = np.array(b64_to_image(data.seed_mask_b64).convert("L")) > 127
            predictor.add_new_mask(
                inference_state=state,
                frame_idx=seed_index,
                obj_id=1,
                mask=seed_mask,
            )
        else:
            assert bbox is not None
            width, height = images[seed_index].size
            box = np.array(
                [
                    bbox["x"] * width,
                    bbox["y"] * height,
                    (bbox["x"] + bbox["w"]) * width,
                    (bbox["y"] + bbox["h"]) * height,
                ],
                dtype=np.float32,
            )
            predictor.add_new_points_or_box(
                inference_state=state,
                frame_idx=seed_index,
                obj_id=1,
                box=box,
            )

        directions = [(seed_index, False), (seed_index, True)]
        for start_index, reverse in directions:
            for frame_index, object_ids, mask_logits in predictor.propagate_in_video(
                state,
                start_frame_idx=start_index,
                max_frame_num_to_track=len(images),
                reverse=reverse,
            ):
                if time.monotonic() - started >= data.max_seconds or await request.is_disconnected():
                    cancelled = True
                    break
                object_id_values = (
                    object_ids.detach().cpu().numpy()
                    if torch.is_tensor(object_ids)
                    else np.asarray(object_ids)
                )
                object_positions = np.where(object_id_values == 1)[0]
                if not len(object_positions):
                    continue
                logits = mask_logits[int(object_positions[0])]
                probabilities = torch.sigmoid(logits).detach().cpu().numpy().squeeze()
                mask = probabilities > 0.5
                confidence = float(probabilities[mask].mean()) if mask.any() else 0.0
                box = _mask_box(mask)
                lost = box is None or confidence < data.lost_confidence
                outputs[int(frame_index)] = {
                    "frame_index": window_start + int(frame_index),
                    "bbox": box,
                    "mask_b64": (
                        mask_to_b64(mask) if data.include_masks and box is not None else None
                    ),
                    "confidence": confidence,
                    "state": "lost" if lost else "tracked",
                }
            if cancelled:
                break
        predictor.reset_state(state)

    frames = [outputs[index] for index in sorted(outputs)]
    return {
        "tracker": "sam2_video",
        "processed_start_index": frames[0]["frame_index"] if frames else window_start,
        "processed_end_index": frames[-1]["frame_index"] if frames else window_start,
        "cancelled": cancelled,
        "frames": frames,
    }


@app.post("/clip/embed")
async def clip_embed(data: dict):
    """Get CLIP embedding for a single image.

    Input: {"image_b64": str}
    Output: {"embedding": list[float]}
    """
    model, preprocess = get_clip()
    image = b64_to_image(data["image_b64"])
    tensor = preprocess(image).unsqueeze(0)
    tensor = tensor.to(get_device())

    with torch.no_grad():
        embedding = model.encode_image(tensor)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)

    return {"embedding": embedding[0].cpu().tolist()}


@app.post("/clip/batch-embed")
async def clip_batch_embed(data: dict):
    """Get CLIP embeddings for a batch of images.

    Input: {"images_b64": list[str]}
    Output: {"embeddings": list[list[float]]}
    """
    model, preprocess = get_clip()
    images = [b64_to_image(b64) for b64 in data["images_b64"]]
    tensors = torch.stack([preprocess(img) for img in images])
    tensors = tensors.to(get_device())

    with torch.no_grad():
        embeddings = model.encode_image(tensors)
        embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)

    return {"embeddings": embeddings.cpu().tolist()}
