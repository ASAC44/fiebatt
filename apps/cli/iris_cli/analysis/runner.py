"""Parallel orchestrator for multi-agent video analysis pipeline."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from .chunk_agent import analyze_chunk
from .extractor import download_video, extract_frames
from .synthesizer import synthesize

console = Console()

CACHE_DIR = Path.home() / ".iris" / "analyses"


async def run_analysis(
    project_id: str,
    video_url: str,
    duration: float,
    api_key: str,
    fps: float = 1.0,
    chunk_size: int = 8,
    max_concurrent: int = 5,
    use_cache: bool = True,
) -> dict:
    """Run the full multi-agent video analysis pipeline.

    Pipeline:
        1. Check cache / download video
        2. Extract frames at fps
        3. Chunk frames into groups of chunk_size
        4. Fan out to parallel Gemini vision calls (bounded by max_concurrent)
        5. Synthesize chunk reports into unified analysis
        6. Cache result and clean up temp files

    Args:
        project_id: iris project ID
        video_url: URL to download the source video from
        duration: total video duration in seconds
        api_key: Gemini API key
        fps: frames per second to sample
        chunk_size: number of frames per analysis chunk
        max_concurrent: max parallel Gemini calls
        use_cache: if True, return cached result when available

    Returns:
        Unified analysis dict
    """
    # Check cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{project_id}.json"
    if use_cache and cache_path.exists():
        console.print(f"[dim]using cached analysis from {cache_path}[/dim]")
        return json.loads(cache_path.read_text())

    # Step 1: Download video
    tmp_dir = Path(tempfile.mkdtemp(prefix="iris-analysis-"))
    video_path = tmp_dir / "video.mp4"

    console.print("[dim]downloading video...[/dim]")
    download_video(video_url, video_path)

    # Step 2: Extract frames
    console.print(f"[dim]extracting frames at {fps} fps...[/dim]")
    frames = extract_frames(video_path, fps=fps, output_dir=tmp_dir / "frames")
    console.print(f"[dim]extracted {len(frames)} frames[/dim]")

    if not frames:
        console.print("[yellow]no frames extracted — video may be too short or corrupt[/yellow]")
        return {
            "project_id": project_id,
            "duration": duration,
            "overall_description": "no frames could be extracted",
            "scenes": [],
            "entities": [],
            "mood_arc": [],
            "lighting_conditions": "unknown",
            "suggested_edits": [],
            "raw_chunks": [],
        }

    # Step 3: Chunk frames
    chunks: list[list[tuple[float, Path]]] = []
    for i in range(0, len(frames), chunk_size):
        chunks.append(frames[i : i + chunk_size])

    console.print(
        f"[dim]analyzing {len(chunks)} chunks "
        f"(max {max_concurrent} concurrent)...[/dim]"
    )

    # Step 4: Fan out with semaphore
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[dict | None] = [None] * len(chunks)

    async def process_chunk(idx: int, chunk: list[tuple[float, Path]]) -> None:
        async with semaphore:
            try:
                result = await analyze_chunk(chunk, api_key=api_key)
                results[idx] = result
            except Exception as exc:
                console.print(f"[red]chunk {idx + 1} failed: {exc}[/red]")
                results[idx] = {
                    "frames_analyzed": [ts for ts, _ in chunk],
                    "error": str(exc),
                    "scene_description": "analysis failed",
                    "objects": [],
                    "entities": [],
                    "mood": "unknown",
                    "lighting": "unknown",
                    "camera_motion": "unknown",
                    "notable_changes": [],
                }

    # Use progress bar when TTY is available, simple prints otherwise
    if console.is_terminal:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("analyzing chunks", total=len(chunks))

            async def tracked_chunk(idx: int, chunk: list[tuple[float, Path]]) -> None:
                await process_chunk(idx, chunk)
                progress.update(task, advance=1)

            await asyncio.gather(
                *(tracked_chunk(i, chunk) for i, chunk in enumerate(chunks))
            )
    else:
        # Non-TTY fallback: simple log lines
        async def logged_chunk(idx: int, chunk: list[tuple[float, Path]]) -> None:
            await process_chunk(idx, chunk)
            console.print(f"[dim]  chunk {idx + 1}/{len(chunks)} done[/dim]")

        await asyncio.gather(
            *(logged_chunk(i, chunk) for i, chunk in enumerate(chunks))
        )

    # Step 5: Synthesize
    chunk_reports = [r for r in results if r is not None]
    console.print("[dim]synthesizing analysis...[/dim]")
    analysis = await synthesize(
        chunk_reports=chunk_reports,
        project_id=project_id,
        duration=duration,
        api_key=api_key,
    )

    # Step 6: Cache
    cache_path.write_text(json.dumps(analysis, indent=2))
    console.print(f"[dim]cached analysis at {cache_path}[/dim]")

    # Cleanup temp files
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return analysis
