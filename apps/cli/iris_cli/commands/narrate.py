"""iris narrate — generate narration for a variant."""

from typing import Optional

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def narrate(
    variant: str = typer.Option(..., "--variant", "-v", help="Variant ID"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Custom narration text"),
) -> None:
    """Generate narration audio for a variant."""
    result = _client().narrate(
        variant_id=variant,
        description=description,
    )
    console.print("[green]Narration generated.[/green]")
    print_result(result)
