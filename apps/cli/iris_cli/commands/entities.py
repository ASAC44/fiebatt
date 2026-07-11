"""iris entity — inspect an entity."""

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def get_entity(
    entity_id: str = typer.Argument(..., help="Entity ID"),
) -> None:
    """Show entity detail and its appearances."""
    data = _client().get_entity(entity_id)
    print_result(data)
