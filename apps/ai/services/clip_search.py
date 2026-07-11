"""CLIP embedding service — runs on Vultr GPU.

Fast entity search via visual similarity instead of batching through Gemini vision.
Embed the reference crop, embed each keyframe, cosine similarity to find matches.
Way faster than 10-keyframe Gemini batches.
"""

import httpx
import base64
from pathlib import Path

from ai.services.config import get_settings


async def embed_image(image_path: str) -> list[float]:
    """Get CLIP embedding for a single image via GPU worker.

    Args:
        image_path: Path to image file

    Returns:
        CLIP embedding vector (list of floats)
    """
    settings = get_settings()
    gpu_url = settings.gpu_worker_url

    image_bytes = Path(image_path).read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode()

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gpu_url}/clip/embed",
            json={"image_b64": image_b64},
        )
        response.raise_for_status()

    return response.json()["embedding"]


async def batch_embed_images(image_paths: list[str]) -> list[list[float]]:
    """Get CLIP embeddings for a batch of images via GPU worker.

    More efficient than individual calls — GPU batches the forward pass.

    Args:
        image_paths: List of paths to image files

    Returns:
        List of CLIP embedding vectors
    """
    settings = get_settings()
    gpu_url = settings.gpu_worker_url

    images_b64 = []
    for path in image_paths:
        image_bytes = Path(path).read_bytes()
        images_b64.append(base64.b64encode(image_bytes).decode())

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{gpu_url}/clip/batch-embed",
            json={"images_b64": images_b64},
        )
        response.raise_for_status()

    return response.json()["embeddings"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def search_keyframes_by_similarity(
    reference_crop_path: str,
    keyframe_paths: list[str],
    threshold: float = 0.75,
) -> list[dict]:
    """Search keyframes for entity by CLIP visual similarity.

    Much faster than Gemini vision batching — embed once, compare all.

    Args:
        reference_crop_path: Path to the cropped entity image
        keyframe_paths: List of keyframe image paths
        threshold: Minimum cosine similarity to consider a match

    Returns:
        List of {"keyframe_index": int, "confidence": float, "found": bool}
    """
    # Embed reference and all keyframes in one batch
    all_paths = [reference_crop_path] + keyframe_paths
    embeddings = await batch_embed_images(all_paths)

    ref_embedding = embeddings[0]
    keyframe_embeddings = embeddings[1:]

    results = []
    for i, kf_emb in enumerate(keyframe_embeddings):
        sim = cosine_similarity(ref_embedding, kf_emb)
        results.append({
            "keyframe_index": i,
            "confidence": round(sim, 3),
            "found": sim >= threshold,
        })

    return results
