"""fiebatt entity — inspect an entity."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def get_entity(
    entity_id: str = typer.Argument(..., help="Entity ID"),
) -> None:
    """Show entity detail and its appearances."""
    data = _client().get_entity(entity_id)
    print_result(data)
