"""fiebatt split — split a timeline segment."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


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
