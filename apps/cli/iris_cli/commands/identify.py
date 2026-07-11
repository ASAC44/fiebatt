"""iris identify — identify an entity in a video region."""

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result
from iris_cli.parsers import parse_bbox

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def identify(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    frame: float = typer.Option(..., "--frame", "-f", help="Frame timestamp (seconds)"),
    bbox: str = typer.Option(..., "--bbox", "-b", help="Bounding box as 'x,y,w,h' (0-1 normalized)"),
) -> None:
    """Identify an entity within a bounding box at a given frame."""
    bbox_dict = parse_bbox(bbox)
    result = _client().identify(
        project_id=project,
        frame_ts=frame,
        bbox=bbox_dict,
    )
    print_result(result)
