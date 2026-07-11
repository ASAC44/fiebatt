"""iris auth — manage authentication and connection settings."""

from typing import Optional

import typer
from rich.console import Console

from iris_cli.config import load_config, save_config, get_client_kwargs
from iris_cli.client import IrisClient
from iris_cli import output
from iris_cli.output import print_json

app = typer.Typer(help="Manage authentication and connection settings.")
console = Console()


@app.command()
def login(
    token: Optional[str] = typer.Option(None, "--token", "-t", help="API auth token"),
    base_url: Optional[str] = typer.Option(None, "--base-url", "-u", help="Backend base URL"),
) -> None:
    """Save authentication token and/or base URL to config."""
    if token is None and base_url is None:
        console.print("[red]Provide --token and/or --base-url[/red]")
        raise typer.Exit(1)

    cfg = load_config()

    if token is not None:
        cfg["token"] = token
        console.print("[green]Token saved.[/green]")

    if base_url is not None:
        cfg["base_url"] = base_url.rstrip("/")
        console.print(f"[green]Base URL set to {cfg['base_url']}[/green]")

    save_config(cfg)


@app.command()
def status() -> None:
    """Show current session, connection, and auth status."""
    cfg = load_config()
    kwargs = get_client_kwargs()

    info = {
        "session_id": cfg["session_id"],
        "base_url": cfg["base_url"],
        "token_set": cfg.get("token") is not None,
    }

    # Try to reach the backend
    try:
        client = IrisClient(**kwargs)
        client.health()
        info["backend_reachable"] = True
    except Exception:
        info["backend_reachable"] = False

    if output.FORMAT == "json":
        print_json(info)
    else:
        console.print(f"[bold]session_id:[/bold]      {info['session_id']}")
        console.print(f"[bold]base_url:[/bold]        {info['base_url']}")
        console.print(f"[bold]token_set:[/bold]       {info['token_set']}")
        reachable = "[green]yes[/green]" if info["backend_reachable"] else "[red]no[/red]"
        console.print(f"[bold]backend_reachable:[/bold] {reachable}")
