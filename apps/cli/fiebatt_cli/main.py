"""fiebatt CLI — command-line interface for the fiebatt AI video editor."""

from typing import Optional

import typer

from fiebatt_cli import output
from fiebatt_cli.commands import auth
from fiebatt_cli.commands import upload as upload_cmd
from fiebatt_cli.commands import projects as projects_cmd
from fiebatt_cli.commands import generate as generate_cmd
from fiebatt_cli.commands import jobs as jobs_cmd
from fiebatt_cli.commands import accept as accept_cmd
from fiebatt_cli.commands import identify as identify_cmd
from fiebatt_cli.commands import mask as mask_cmd
from fiebatt_cli.commands import entities as entities_cmd
from fiebatt_cli.commands import propagate as propagate_cmd
from fiebatt_cli.commands import timeline as timeline_cmd
from fiebatt_cli.commands import preview as preview_cmd
from fiebatt_cli.commands import split as split_cmd
from fiebatt_cli.commands import trim as trim_cmd
from fiebatt_cli.commands import snapshot as snapshot_cmd
from fiebatt_cli.commands import grade as grade_cmd
from fiebatt_cli.commands import score as score_cmd
from fiebatt_cli.commands import remix as remix_cmd
from fiebatt_cli.commands import batch as batch_cmd
from fiebatt_cli.commands import narrate as narrate_cmd
from fiebatt_cli.commands import export as export_cmd
from fiebatt_cli.commands import analyze as analyze_cmd

app = typer.Typer(
    name="fiebatt",
    help="CLI for the fiebatt AI video editor.",
    no_args_is_help=True,
)

# Group the public CLI by resource. The original flat commands remain
# available as hidden aliases for existing scripts.
projects_app = typer.Typer(
    help="Upload, inspect, and manage video projects.",
    invoke_without_command=True,
    no_args_is_help=False,
)
edits_app = typer.Typer(help="Create, refine, and apply video edits.")
jobs_app = typer.Typer(help="Inspect asynchronous jobs.")
entities_app = typer.Typer(help="Inspect tracked video entities.")
batch_app = typer.Typer(help="Run generation and acceptance in batches.")


@app.callback()
def main(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    human_output: bool = typer.Option(False, "--human", help="Output as human-readable (default)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Override backend base URL for this command"),
) -> None:
    """fiebatt — AI video editor CLI."""
    if json_output:
        output.FORMAT = "json"
    elif human_output:
        output.FORMAT = "human"

    if base_url is not None:
        # Patch the config module for this invocation
        from fiebatt_cli import config
        _original_get = config.get_client_kwargs

        def _patched() -> dict:
            kwargs = _original_get()
            kwargs["base_url"] = base_url.rstrip("/")
            return kwargs

        config.get_client_kwargs = _patched  # type: ignore[assignment]


# ── Register command groups ─────────────────────────────────────────────

app.add_typer(auth.app, name="auth")
app.add_typer(projects_app, name="projects")
app.add_typer(edits_app, name="edits")
app.add_typer(jobs_app, name="jobs")
app.add_typer(entities_app, name="entities")
app.add_typer(batch_app, name="batch")


@projects_app.callback()
def projects(ctx: typer.Context) -> None:
    """List projects when no project subcommand is supplied."""
    if ctx.invoked_subcommand is None:
        projects_cmd.list_projects()


projects_app.command(name="get")(projects_cmd.get_project)
projects_app.command(name="upload")(upload_cmd.upload)
projects_app.command(name="analyze")(analyze_cmd.analyze)
projects_app.command(name="timeline")(timeline_cmd.timeline)
projects_app.command(name="preview")(preview_cmd.preview)
projects_app.command(name="snapshot")(snapshot_cmd.snapshot)
projects_app.command(name="revert")(snapshot_cmd.revert)
projects_app.command(name="export")(export_cmd.export)

edits_app.command(name="identify")(identify_cmd.identify)
edits_app.command(name="mask")(mask_cmd.mask)
edits_app.command(name="generate")(generate_cmd.generate)
edits_app.command(name="accept")(accept_cmd.accept)
edits_app.command(name="propagate")(propagate_cmd.propagate)
edits_app.command(name="split")(split_cmd.split)
edits_app.command(name="trim")(trim_cmd.trim)
edits_app.command(name="grade")(grade_cmd.grade)
edits_app.command(name="score")(score_cmd.score)
edits_app.command(name="remix")(remix_cmd.remix)
edits_app.command(name="narrate")(narrate_cmd.narrate)

jobs_app.command(name="get")(jobs_cmd.get_job)
entities_app.command(name="get")(entities_cmd.get_entity)
batch_app.command(name="generate")(batch_cmd.batch_generate)
batch_app.command(name="accept")(batch_cmd.batch_accept)

# ── Register top-level commands ─────────────────────────────────────────

app.command(name="upload", hidden=True)(upload_cmd.upload)
app.command(name="project", hidden=True)(projects_cmd.get_project)
app.command(name="generate", hidden=True)(generate_cmd.generate)
app.command(name="job", hidden=True)(jobs_cmd.get_job)
app.command(name="accept", hidden=True)(accept_cmd.accept)
app.command(name="identify", hidden=True)(identify_cmd.identify)
app.command(name="mask", hidden=True)(mask_cmd.mask)
app.command(name="entity", hidden=True)(entities_cmd.get_entity)
app.command(name="propagate", hidden=True)(propagate_cmd.propagate)
app.command(name="timeline", hidden=True)(timeline_cmd.timeline)
app.command(name="preview", hidden=True)(preview_cmd.preview)
app.command(name="split", hidden=True)(split_cmd.split)
app.command(name="trim", hidden=True)(trim_cmd.trim)
app.command(name="snapshot", hidden=True)(snapshot_cmd.snapshot)
app.command(name="revert", hidden=True)(snapshot_cmd.revert)
app.command(name="grade", hidden=True)(grade_cmd.grade)
app.command(name="score", hidden=True)(score_cmd.score)
app.command(name="remix", hidden=True)(remix_cmd.remix)
app.command(name="batch-generate", hidden=True)(batch_cmd.batch_generate)
app.command(name="batch-accept", hidden=True)(batch_cmd.batch_accept)
app.command(name="narrate", hidden=True)(narrate_cmd.narrate)
app.command(name="export", hidden=True)(export_cmd.export)
app.command(name="analyze", hidden=True)(analyze_cmd.analyze)


if __name__ == "__main__":
    app()
