"""fiebatt trim — trim a timeline segment."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def trim(
    project: str = typer.Option(..., "--project", help="Project ID"),
    segment: str = typer.Option(..., "--segment", help="Segment ID"),
    start: float = typer.Option(..., "--start", help="New start timestamp (seconds)"),
    end: float = typer.Option(..., "--end", help="New end timestamp (seconds)"),
) -> None:
    """Trim a segment to a new range."""
    result = _client().trim_segment(
        project_id=project,
        segment_id=segment,
        new_start_ts=start,
        new_end_ts=end,
    )
    print_result(result)
