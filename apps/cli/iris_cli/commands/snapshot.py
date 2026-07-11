"""iris snapshot / revert — timeline snapshot commands."""

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


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
