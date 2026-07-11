"""iris analyze — multi-agent video analysis command."""

import asyncio
import os

import typer

from ..client import IrisClient
from ..config import load_config
from ..output import FORMAT, print_json, print_status

app = typer.Typer()


@app.command()
def analyze(
    project_id: str = typer.Argument(help="Project ID to analyze"),
    fps: float = typer.Option(1.0, help="Frames per second to sample"),
    chunk_size: int = typer.Option(8, help="Frames per analysis chunk"),
    max_concurrent: int = typer.Option(5, help="Max concurrent Gemini calls"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Skip cache, re-analyze"),
) -> None:
    """Analyze a video using multi-agent vision.

    Extracts frames, fans out analysis to parallel Gemini vision calls,
    and synthesizes a comprehensive understanding of the video content.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        typer.echo("error: GEMINI_API_KEY environment variable required", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    client = IrisClient(
        base_url=cfg["base_url"],
        session_id=cfg["session_id"],
        token=cfg.get("token"),
    )

    # Get project metadata from backend
    project = client.get_project(project_id)
    video_url: str = project["video_url"]
    duration: float = project["duration"]

    from ..analysis.runner import run_analysis

    result = asyncio.run(
        run_analysis(
            project_id=project_id,
            video_url=video_url,
            duration=duration,
            api_key=api_key,
            fps=fps,
            chunk_size=chunk_size,
            max_concurrent=max_concurrent,
            use_cache=not no_cache,
        )
    )

    if FORMAT == "json":
        print_json(result)
    else:
        print_status("project", project_id)
        print_status("duration", f"{result.get('duration', 0):.1f}s")
        print_status("scenes", str(len(result.get("scenes", []))))
        print_status("entities", str(len(result.get("entities", []))))
        print_status("suggested edits", str(len(result.get("suggested_edits", []))))
        typer.echo()
        typer.echo(result.get("overall_description", ""))
        if result.get("suggested_edits"):
            typer.echo()
            typer.echo("suggested edits:")
            for edit in result["suggested_edits"]:
                typer.echo(
                    f"  [{edit['start_ts']:.1f}s - {edit['end_ts']:.1f}s] "
                    f"{edit['suggestion']}"
                )
                typer.echo(f"    rationale: {edit['rationale']}")
