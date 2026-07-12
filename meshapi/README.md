# Mesh API integration

fiebatt can route its text planning and agent tool-calling layer through Mesh
API, while keeping the rest of the video pipeline unchanged.

Mesh API is OpenAI-compatible, so the integration is intentionally small:

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.environ["MESH_API_KEY"],
    base_url=os.environ.get("MESH_API_BASE_URL", "https://api.meshapi.ai/v1"),
)
```

## Default model

The project defaults to:

```text
deepseek/deepseek-v3.2
```

That model is used for the agent's text reasoning, prompt rewriting, tool
selection, and structured edit planning when `MESH_API_KEY` is present.

Vision-specific calls still prefer the existing vision provider when a request
contains image payloads, because the default Mesh model above is a text model.

## Environment

```bash
MESH_API_KEY="mesh_sk_..."
MESH_API_BASE_URL="https://api.meshapi.ai/v1"
MESH_MODEL="deepseek/deepseek-v3.2"
MESH_VIDEO_MODEL="google/veo-3"
MESH_VIDEO_ENDPOINT="/videos/generations"
USE_AI_STUBS="false"
```

With those values set, `/api/agent/chat` will use Mesh API for the agent loop.

To route video generation through Mesh as well:

```bash
VIDEO_GEN_PROVIDER="meshapi_veo"
```

The video adapter is intentionally configurable because Mesh model IDs vary by
dashboard availability. Keep `MESH_VIDEO_MODEL` aligned with the Veo 3 model ID
shown in your Mesh account.
