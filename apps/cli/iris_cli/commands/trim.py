"""iris trim — trim a timeline segment."""

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


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
