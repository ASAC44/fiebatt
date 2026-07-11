"""iris generate — start a generation job."""

from typing import Optional

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result
from iris_cli.parsers import parse_bbox
from iris_cli.poll import poll_job

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def generate(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    start: float = typer.Option(..., "--start", "-s", help="Start timestamp (seconds)"),
    end: float = typer.Option(..., "--end", "-e", help="End timestamp (seconds)"),
    bbox: str = typer.Option(..., "--bbox", "-b", help="Bounding box as 'x,y,w,h' (0-1 normalized)"),
    prompt: str = typer.Option(..., "--prompt", help="Generation prompt"),
    ref_frame: Optional[float] = typer.Option(None, "--ref-frame", help="Reference frame timestamp (defaults to start)"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Don't poll for completion"),
) -> None:
    """Start a generation job for a video segment."""
    bbox_dict = parse_bbox(bbox)
    reference_frame_ts = ref_frame if ref_frame is not None else start

    client = _client()
    result = client.generate(
        project_id=project,
        start_ts=start,
        end_ts=end,
        bbox=bbox_dict,
        prompt=prompt,
        reference_frame_ts=reference_frame_ts,
    )

    job_id = result.get("job_id", "")
    console.print(f"[green]Generation job started:[/green] {job_id}")

    if no_wait:
        print_result(result)
        return

    console.print("Polling for completion...")
    final = poll_job(client, job_id)
    print_result(final)
