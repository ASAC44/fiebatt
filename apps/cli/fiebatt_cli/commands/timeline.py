"""fiebatt timeline — show project timeline."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def timeline(
    project_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Show the ordered timeline segments for a project."""
    data = _client().get_timeline(project_id)
    print_result(data)
