"""iris CLI — command-line interface for the iris AI video editor."""

from typing import Optional

import typer

from iris_cli import output
from iris_cli.commands import auth
from iris_cli.commands import upload as upload_cmd
from iris_cli.commands import projects as projects_cmd
from iris_cli.commands import generate as generate_cmd
from iris_cli.commands import jobs as jobs_cmd
from iris_cli.commands import accept as accept_cmd
from iris_cli.commands import identify as identify_cmd
from iris_cli.commands import mask as mask_cmd
from iris_cli.commands import entities as entities_cmd
from iris_cli.commands import propagate as propagate_cmd
from iris_cli.commands import timeline as timeline_cmd
from iris_cli.commands import preview as preview_cmd
from iris_cli.commands import split as split_cmd
from iris_cli.commands import trim as trim_cmd
from iris_cli.commands import snapshot as snapshot_cmd
from iris_cli.commands import grade as grade_cmd
from iris_cli.commands import score as score_cmd
from iris_cli.commands import remix as remix_cmd
from iris_cli.commands import batch as batch_cmd
from iris_cli.commands import narrate as narrate_cmd
from iris_cli.commands import export as export_cmd
from iris_cli.commands import analyze as analyze_cmd

app = typer.Typer(
    name="iris",
    help="CLI for the iris AI video editor.",
    no_args_is_help=True,
)


@app.callback()
def main(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    human_output: bool = typer.Option(False, "--human", help="Output as human-readable (default)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Override backend base URL for this command"),
) -> None:
    """iris — AI video editor CLI."""
    if json_output:
        output.FORMAT = "json"
    elif human_output:
        output.FORMAT = "human"

    if base_url is not None:
        # Patch the config module for this invocation
        from iris_cli import config
        _original_get = config.get_client_kwargs

        def _patched() -> dict:
            kwargs = _original_get()
            kwargs["base_url"] = base_url.rstrip("/")
            return kwargs

        config.get_client_kwargs = _patched  # type: ignore[assignment]


# ── Register command groups ─────────────────────────────────────────────

app.add_typer(auth.app, name="auth")

# ── Register top-level commands ─────────────────────────────────────────

app.command(name="upload")(upload_cmd.upload)
app.command(name="projects")(projects_cmd.list_projects)
app.command(name="project")(projects_cmd.get_project)
app.command(name="generate")(generate_cmd.generate)
app.command(name="job")(jobs_cmd.get_job)
app.command(name="accept")(accept_cmd.accept)
app.command(name="identify")(identify_cmd.identify)
app.command(name="mask")(mask_cmd.mask)
app.command(name="entity")(entities_cmd.get_entity)
app.command(name="propagate")(propagate_cmd.propagate)
app.command(name="timeline")(timeline_cmd.timeline)
app.command(name="preview")(preview_cmd.preview)
app.command(name="split")(split_cmd.split)
app.command(name="trim")(trim_cmd.trim)
app.command(name="snapshot")(snapshot_cmd.snapshot)
app.command(name="revert")(snapshot_cmd.revert)
app.command(name="grade")(grade_cmd.grade)
app.command(name="score")(score_cmd.score)
app.command(name="remix")(remix_cmd.remix)
app.command(name="batch-generate")(batch_cmd.batch_generate)
app.command(name="batch-accept")(batch_cmd.batch_accept)
app.command(name="narrate")(narrate_cmd.narrate)
app.command(name="export")(export_cmd.export)
app.command(name="analyze")(analyze_cmd.analyze)


if __name__ == "__main__":
    app()
