---
title: Fiebatt SAM2
emoji: ✂️
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.49.1
python_version: 3.12
app_file: app.py
pinned: false
short_description: SAM2 bounding-box segmentation endpoint for Fiebatt
preload_from_hub:
  - facebook/sam2.1-hiera-small sam2.1_hiera_small.pt
---

# Fiebatt SAM2 endpoint

ZeroGPU-backed SAM2.1 segmentation for Fiebatt. The public API accepts the
same JSON payload as the self-hosted vision worker:

```json
{
  "image_b64": "<base64 encoded image>",
  "bbox": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}
}
```

Call the named Gradio endpoint with `gradio_client`:

```python
from gradio_client import Client

client = Client("https://YOUR-NAME-fiebatt-sam2.hf.space")
result = client.predict(
    {
        "image_b64": image_b64,
        "bbox": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
    },
    api_name="/segment",
)
```

The result contains `mask_b64`, `score`, and `candidate_count`. Set the
Fiebatt API's `SAM_SEGMENTATION_URL` to the Space's `*.hf.space` URL. Keep
`VISION_WORKER_URL` pointed at the full worker, because the Space does not
provide bounded video tracking or embedding endpoints.
