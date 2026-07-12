"""fiebatt score — score variants and project continuity."""

from typing import Optional

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def score(
    variant: Optional[str] = typer.Option(None, "--variant", help="Variant ID"),
    compare: Optional[tuple[str, str]] = typer.Option(
        None,
        "--compare",
        help="Variant IDs to compare",
    ),
    continuity: Optional[str] = typer.Option(None, "--continuity", help="Project ID to score for continuity"),
    compare_to: str = typer.Option("prompt", "--compare-to", help="Comparison target for variant scoring"),
) -> None:
    """Score a variant, compare variants, or score project continuity."""
    modes_selected = sum(
        value is not None
        for value in (variant, compare, continuity)
    )
    if modes_selected != 1:
        raise typer.BadParameter("use exactly one of --variant, --compare, or --continuity")

    client = _client()

    if variant is not None:
        result = client.score_variant(variant_id=variant, compare_to=compare_to)
        print_result(result)
        return

    if compare is not None:
        result = client.score_compare(variant_ids=list(compare))
        print_result(result)
        return

    result = client.score_continuity(project_id=continuity or "")
    print_result(result)
