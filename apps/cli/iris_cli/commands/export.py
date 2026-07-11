"""iris export — export a project to final video."""

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result
from iris_cli.poll import poll_export

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def export(
    project_id: str = typer.Argument(..., help="Project ID"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Don't poll for completion"),
) -> None:
    """Export the project as a final video."""
    client = _client()
    result = client.export_video(project_id)

    export_job_id = result.get("export_job_id", "")
    console.print(f"[green]Export job started:[/green] {export_job_id}")

    if no_wait:
        print_result(result)
        return

    console.print("Polling for completion...")
    final = poll_export(client, export_job_id)
    print_result(final)
