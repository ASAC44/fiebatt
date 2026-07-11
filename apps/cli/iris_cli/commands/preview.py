"""iris preview — preview frames and ranges."""

from typing import Optional

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def preview(
    project_id: str = typer.Argument(..., help="Project ID"),
    frame: Optional[float] = typer.Option(None, "--frame", help="Preview a single frame timestamp"),
    strip: Optional[tuple[float, float]] = typer.Option(
        None,
        "--strip",
        help="Preview strip start and end timestamps",
    ),
    fps: float = typer.Option(1.0, "--fps", help="Frames per second for strip previews"),
    range_values: Optional[tuple[float, float]] = typer.Option(
        None,
        "--range",
        help="Preview range start and end timestamps",
    ),
) -> None:
    """Preview a frame, strip, or range for a project."""
    modes_selected = sum(
        value is not None
        for value in (frame, strip, range_values)
    )
    if modes_selected != 1:
        raise typer.BadParameter("use exactly one of --frame, --strip, or --range")

    client = _client()

    if frame is not None:
        result = client.preview_frame(project_id=project_id, ts=frame)
        print_result(result)
        return

    if strip is not None:
        result = client.preview_strip(
            project_id=project_id,
            start=strip[0],
            end=strip[1],
            fps=fps,
        )
        print_result(result)
        return

    if range_values is None:
        raise typer.BadParameter("--range requires exactly two values: start end")

    result = client.preview_range(
        project_id=project_id,
        start=range_values[0],
        end=range_values[1],
    )
    print_result(result)
