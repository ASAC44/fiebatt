"""fiebatt grade — grade a segment."""

from typing import Optional

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def grade(
    segment: str = typer.Option(..., "--segment", help="Segment ID"),
    brightness: Optional[float] = typer.Option(None, "--brightness", help="Brightness adjustment"),
    saturation: Optional[float] = typer.Option(None, "--saturation", help="Saturation adjustment"),
    preview: bool = typer.Option(False, "--preview", help="Preview the grade without applying it"),
) -> None:
    """Apply or preview grading adjustments for a segment."""
    adjustments = {
        key: value
        for key, value in {
            "brightness": brightness,
            "saturation": saturation,
        }.items()
        if value is not None
    }
    if not adjustments:
        raise typer.BadParameter("provide at least one grading adjustment")

    client = _client()
    if preview:
        result = client.grade_preview(segment_id=segment, adjustments=adjustments)
    else:
        result = client.grade_segment(segment_id=segment, adjustments=adjustments)
    print_result(result)
