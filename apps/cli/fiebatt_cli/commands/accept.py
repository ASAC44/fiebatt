"""fiebatt accept — accept a generated variant."""

import typer
from rich.console import Console

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result

console = Console()


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def accept(
    job: str = typer.Option(..., "--job", "-j", help="Job ID"),
    variant: int = typer.Option(0, "--variant", "-v", help="Variant index to accept"),
) -> None:
    """Accept a variant from a completed generation job."""
    client = _client()
    result = client.accept(job_id=job, variant_index=variant)
    console.print("[green]Variant accepted.[/green]")
    print_result(result)
