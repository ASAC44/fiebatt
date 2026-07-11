"""iris split — split a timeline segment."""

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def split(
    project: str = typer.Option(..., "--project", help="Project ID"),
    segment: str = typer.Option(..., "--segment", help="Segment ID"),
    at: float = typer.Option(..., "--at", help="Split timestamp (seconds)"),
) -> None:
    """Split a segment at a timestamp."""
    result = _client().split_segment(
        project_id=project,
        segment_id=segment,
        split_ts=at,
    )
    print_result(result)
