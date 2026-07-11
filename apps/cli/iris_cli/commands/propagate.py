"""iris propagate — propagate edits across entity appearances."""

import typer
from rich.console import Console

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result
from iris_cli.poll import poll_propagation

console = Console()


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def propagate(
    entity: str = typer.Option(..., "--entity", "-e", help="Entity ID"),
    source_url: str = typer.Option(..., "--source-url", "-s", help="Source variant URL"),
    prompt: str = typer.Option(..., "--prompt", help="Propagation prompt"),
    no_auto_apply: bool = typer.Option(False, "--no-auto-apply", help="Don't auto-apply results"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Don't poll for completion"),
) -> None:
    """Propagate edits to all appearances of an entity."""
    client = _client()
    result = client.propagate(
        entity_id=entity,
        source_variant_url=source_url,
        prompt=prompt,
        auto_apply=not no_auto_apply,
    )

    prop_job_id = result.get("propagation_job_id", "")
    console.print(f"[green]Propagation job started:[/green] {prop_job_id}")

    if no_wait:
        print_result(result)
        return

    console.print("Polling for completion...")
    final = poll_propagation(client, prop_job_id)
    print_result(final)
