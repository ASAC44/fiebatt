"""iris upload — upload a video file."""

from pathlib import Path

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def upload(
    video_path: Path = typer.Argument(..., help="Path to the video file to upload", exists=True),
) -> None:
    """Upload a video file and create a new project."""
    console.print(f"Uploading [bold]{video_path.name}[/bold]...")
    result = _client().upload(video_path)
    console.print("[green]Upload complete.[/green]")
    print_result(result)
