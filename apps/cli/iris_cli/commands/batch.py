"""iris batch-generate / batch-accept — batch editing commands."""

import json
from pathlib import Path
from typing import Any

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def _load_json_list(path: Path, label: str) -> list[Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise typer.BadParameter(f"{label} file must contain a JSON list")
    return data


def batch_generate(
    edits: Path = typer.Option(..., "--edits", help="Path to edits JSON file", exists=True),
) -> None:
    """Submit a batch generate request from a JSON file."""
    edits_data = _load_json_list(edits, "edits")
    result = _client().batch_generate(edits=edits_data)
    print_result(result)


def batch_accept(
    accepts: Path = typer.Option(..., "--accepts", help="Path to accepts JSON file", exists=True),
) -> None:
    """Submit a batch accept request from a JSON file."""
    accepts_data = _load_json_list(accepts, "accepts")
    result = _client().batch_accept(accepts=accepts_data)
    print_result(result)
