"""iris timeline — show project timeline."""

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def timeline(
    project_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Show the ordered timeline segments for a project."""
    data = _client().get_timeline(project_id)
    print_result(data)
