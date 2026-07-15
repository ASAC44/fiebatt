"""SAM2 segmentation worker on Modal.

Replaces the self-hosted vision worker's SAM endpoint with a Modal function.
Deploy: `modal deploy apps/vision-worker/modal_worker.py`
Test:   `modal run apps/vision-worker/modal_worker.py`

The fiebatt backend calls the deployed web endpoint at:
  https://<your-modal-username>--fiebatt-sam2-segment.modal.run
"""

import modal

app = modal.App("fiebatt-sam2")

# build the image with SAM2 + dependencies
sam_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.1.0",
        "torchvision>=0.16.0",
        "numpy",
        "Pillow",
        "sam2>=1.1.0",
        "fastapi[standard]",
    )
)

CHECKPOINT_URL = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt"
CHECKPOINT_PATH = "/root/sam2_hiera_small.pt"
SAM_CONFIG = "configs/sam2.1/sam2.1_hiera_s.yaml"


@app.cls(
    image=sam_image,
    gpu="T4",
    timeout=120,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=4)
class SAM2Worker:
    @modal.enter()
    def load_model(self):
        import os
        import urllib.request
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        # download checkpoint if not cached
        if not os.path.exists(CHECKPOINT_PATH):
            print(f"downloading SAM2 checkpoint to {CHECKPOINT_PATH}...")
            urllib.request.urlretrieve(CHECKPOINT_URL, CHECKPOINT_PATH)

        self.predictor = SAM2ImagePredictor(
            build_sam2(SAM_CONFIG, CHECKPOINT_PATH, device="cuda")
        )

    @modal.fastapi_endpoint(method="POST")
    def segment(self, data: dict):
        import base64
        import io
        import numpy as np
        from PIL import Image

        # decode image
        image = Image.open(io.BytesIO(base64.b64decode(data["image_b64"]))).convert("RGB")
        bbox = data["bbox"]
        w, h = image.size

        # convert normalized bbox to pixel coords [x1, y1, x2, y2]
        box = np.array([
            bbox["x"] * w,
            bbox["y"] * h,
            (bbox["x"] + bbox["w"]) * w,
            (bbox["y"] + bbox["h"]) * h,
        ])

        # run SAM2
        self.predictor.set_image(np.array(image))
        masks, scores, _ = self.predictor.predict(box=box, multimask_output=True)
        best_index = int(np.argmax(scores))
        best_mask = masks[best_index]

        # encode mask as base64 PNG
        mask_img = Image.fromarray((best_mask * 255).astype(np.uint8))
        buf = io.BytesIO()
        mask_img.save(buf, format="PNG")
        mask_b64 = base64.b64encode(buf.getvalue()).decode()

        return {
            "mask_b64": mask_b64,
            "score": float(scores[best_index]),
            "candidate_count": int(len(masks)),
        }

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {"status": "ok", "gpu": True, "device": "cuda", "provider": "modal"}


# ─── local test ──────────────────────────────────────────────────────

@app.local_entrypoint()
def main():
    """Quick smoke test — generates a dummy image and segments it."""
    import base64
    import io
    from PIL import Image

    # create a simple test image (red square on white background)
    img = Image.new("RGB", (256, 256), "white")
    for x in range(64, 192):
        for y in range(64, 192):
            img.putpixel((x, y), (255, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    worker = SAM2Worker()
    result = worker.segment.remote({
        "image_b64": img_b64,
        "bbox": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
    })
    print(f"mask received: {len(result['mask_b64'])} chars")
    print("SAM2 on Modal is working")
