"""iris projects / project — list and inspect projects."""

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_table, print_result

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def list_projects() -> None:
    """List all projects."""
    data = _client().list_projects()
    if isinstance(data, list) and data:
        print_table(
            data,
            columns=["id", "video_url", "duration", "fps", "width", "height"],
        )
    else:
        print_result(data)


def get_project(
    project_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Show project detail with segments and entities."""
    data = _client().get_project(project_id)
    print_result(data)
