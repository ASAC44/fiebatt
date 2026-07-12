"""fiebatt remix — remix a generated variant."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def remix(
    variant: str = typer.Option(..., "--variant", help="Variant ID"),
    modifier: str = typer.Option(..., "--modifier", help="Modifier prompt"),
    preserve_composition: bool = typer.Option(
        True,
        "--preserve-composition/--no-preserve-composition",
        help="Preserve the original composition while remixing",
    ),
) -> None:
    """Create a remix from an existing variant."""
    result = _client().remix(
        variant_id=variant,
        modifier_prompt=modifier,
        preserve_composition=preserve_composition,
    )
    print_result(result)
