"""fiebatt snapshot / revert — timeline snapshot commands."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def snapshot(
    project_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Create a timeline snapshot for a project."""
    result = _client().snapshot_timeline(project_id=project_id)
    print_result(result)


def revert(
    project_id: str = typer.Argument(..., help="Project ID"),
    snapshot: str = typer.Option(..., "--snapshot", help="Snapshot ID"),
) -> None:
    """Revert a project timeline to a snapshot."""
    result = _client().revert_timeline(project_id=project_id, snapshot_id=snapshot)
    print_result(result)
