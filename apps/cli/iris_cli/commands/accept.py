"""iris accept — accept a generated variant."""

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def accept(
    job: str = typer.Option(..., "--job", "-j", help="Job ID"),
    variant: int = typer.Option(0, "--variant", "-v", help="Variant index to accept"),
) -> None:
    """Accept a variant from a completed generation job."""
    client = _client()
    result = client.accept(job_id=job, variant_index=variant)
    console.print("[green]Variant accepted.[/green]")
    print_result(result)
