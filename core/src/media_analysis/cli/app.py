"""Command-line interface: runs a workflow and renders its events (cli/sinks.py)."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from media_analysis.cli.sinks import ConsoleSink, JsonSink
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.settings import USER_TEMPLATES_DIR, Settings
from media_analysis.workflows.report.workflow import ReportOptions, ReportWorkflow

STAGES = ReportWorkflow.stage_names()

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Analyse folders of video and audio.")
templates_app = typer.Typer(no_args_is_help=True, help="List or create report templates.")
app.add_typer(templates_app, name="templates")
console = Console()


def report(
    input_dir: Annotated[Path, typer.Argument(help="Folder with the video/audio files to analyse.")],
    output_dir: Annotated[Path, typer.Argument(help="Folder to write the report into (new, empty, or a previous output).")],
    template: Annotated[Optional[str], typer.Option("--template", "-t", help="Use this template instead of detecting one.")] = None,
    force: Annotated[Optional[str], typer.Option(help=f"Redo this stage and all later ones: {', '.join(STAGES[1:])}.")] = None,
    clean_cache: Annotated[bool, typer.Option(help="Delete the working cache after a successful run.")] = False,
    fresh: Annotated[bool, typer.Option(help="Delete the working cache of earlier runs first and redo every stage.")] = False,
    whisper_model: Annotated[Optional[str], typer.Option(help="faster-whisper model, e.g. large-v3, large-v3-turbo, medium.")] = None,
    device: Annotated[Optional[str], typer.Option(help="Transcription device: auto, cpu or cuda.")] = None,
    language: Annotated[Optional[str], typer.Option(help="Spoken language hint (en, vi). Default: detect, mixed allowed.")] = None,
    backend: Annotated[Optional[str], typer.Option(help="Claude access: subscription (default) or api.")] = None,
    claude_model: Annotated[Optional[str], typer.Option(help="Claude model id. Default: your account's default model.")] = None,
    effort: Annotated[Optional[str], typer.Option(help="Claude thinking effort: low, medium, high, xhigh, max. Default: the model's default.")] = None,
    claude_profile: Annotated[Optional[Path], typer.Option(help="Claude Code config folder whose login to use, e.g. ~/.claude-work. Default: Claude Code's default.")] = None,
    max_frames: Annotated[Optional[int], typer.Option(help="Maximum number of moments to capture as evidence.")] = None,
    templates_dir: Annotated[Optional[list[Path]], typer.Option(help="Extra folder(s) with user templates.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print progress as JSON Lines on stdout instead of the console view.")] = False,
    project_name: Annotated[Optional[str], typer.Option(help="Name of the project to record in the run statistics. Default: the folder name.")] = None,
    log_file: Annotated[Optional[Path], typer.Option(help="Log file of this run, to record in the run statistics.")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Analyse INPUT_DIR and write report.md and reference/ into OUTPUT_DIR."""
    if force and force not in STAGES[1:]:
        raise typer.BadParameter(f"must be one of: {', '.join(STAGES[1:])}", param_hint="--force")
    settings = Settings()
    if whisper_model:
        settings.transcription.model = whisper_model
    if device:
        if device not in ("auto", "cpu", "cuda"):
            raise typer.BadParameter("must be auto, cpu or cuda", param_hint="--device")
        settings.transcription.device = device  # type: ignore[assignment]
    if language:
        settings.transcription.language = language
    if backend:
        if backend not in ("subscription", "api"):
            raise typer.BadParameter("must be subscription or api", param_hint="--backend")
        settings.llm.backend = backend  # type: ignore[assignment]
    if claude_model:
        settings.llm.model = claude_model
    if effort:
        if effort not in EFFORT_LEVELS:
            raise typer.BadParameter(f"must be one of: {', '.join(EFFORT_LEVELS)}", param_hint="--effort")
        settings.llm.effort = effort  # type: ignore[assignment]
    if claude_profile:
        settings.llm.claude_profile = claude_profile
    if max_frames is not None:
        settings.evidence.max_requests = max_frames
    if templates_dir:
        settings.templates_dirs = [*templates_dir, *settings.templates_dirs]

    sink: ConsoleSink | JsonSink = JsonSink() if json_output else ConsoleSink(console, verbose)
    try:
        result = ReportWorkflow(settings, sink).run(ReportOptions(
            input_dir=input_dir, output_dir=output_dir, template_id=template, force_from=force, clean_cache=clean_cache,
            project_name=project_name, log_file=log_file, fresh=fresh,
        ))
    except MediaAnalysisError as e:
        sink.close()
        if isinstance(sink, JsonSink):
            sink.emit({"event": "error", "exit_code": e.exit_code, "message": str(e)})
        else:
            console.print(f"\n[bold red]Error:[/] {e}")
        raise typer.Exit(e.exit_code) from None
    except Exception as e:
        sink.close()
        # The traceback goes to stderr (the tray app keeps it in the run's log); callers get a one-line reason.
        traceback.print_exc()
        if isinstance(sink, JsonSink):
            sink.emit({"event": "error", "exit_code": 1, "message": f"Unexpected error: {type(e).__name__}: {e}"})
        raise typer.Exit(1) from None
    finally:
        sink.close()
    c = result.classification
    if isinstance(sink, JsonSink):
        sink.emit({
            "event": "finished", "report": str(result.report_path), "template": c.template_id,
            "generic": c.fallback, "confidence": c.confidence, "reason": c.reason,
            "evidence_frames": result.evidence_count,
        })
        return
    console.print(f"\n[bold green]Done.[/] Report: {result.report_path}")
    if c.fallback:
        console.print(f"No template fits ({c.reason}); wrote a general report. See its 'Template fit' section.")
    else:
        console.print(f"Processing type: {c.template_id}" + ("" if c.forced else f" ({c.reason})"))


app.command("report")(report)
# The name used before the core had more than one workflow; older Windows app builds still call it.
app.command("run", hidden=True)(report)


@app.command()
def doctor(
    check_claude: Annotated[bool, typer.Option(help="Also make one small Claude call to confirm the login works.")] = False,
    claude_profile: Annotated[Optional[Path], typer.Option(help="Claude Code config folder whose login to check.")] = None,
) -> None:
    """Check ffmpeg, GPU/CPU, whisper and Claude access."""
    from media_analysis.engine.hardware import detect
    from media_analysis.media import ffmpeg

    ok = True
    table = Table(show_header=False, box=None)
    try:
        table.add_row("ffmpeg", f"[green]{ffmpeg.version()}[/]")
        table.add_row("", f"[dim]{ffmpeg.find_binary('ffmpeg')}[/]")
        ffmpeg.find_binary("ffprobe")
    except MediaAnalysisError as e:
        ok = False
        table.add_row("ffmpeg", f"[red]{e}[/]")

    hw = detect(list_gpus=True)
    table.add_row("GPUs", ", ".join(hw.gpus) or "none found")
    if hw.cuda_devices:
        table.add_row("transcription", f"[green]CUDA GPU ({hw.cuda_devices} device(s)), float16[/]")
    else:
        table.add_row("transcription", f"CPU, int8, {hw.cpu_threads} threads [dim](no CUDA GPU; only NVIDIA GPUs are used)[/]")

    models_dir = Settings().transcription.models_dir
    table.add_row("whisper models", f"shared: {models_dir}" if models_dir else "user cache (~/.cache/huggingface)")

    if os.environ.get("ANTHROPIC_API_KEY"):
        table.add_row(
            "Claude",
            "[yellow]ANTHROPIC_API_KEY is set: the api backend would use it; the subscription backend ignores it[/]",
        )
    settings = Settings()
    if claude_profile:
        settings.llm.claude_profile = claude_profile
    profile = settings.llm.profile_dir()
    if (profile / ".credentials.json").exists() or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        table.add_row("Claude login", f"[green]found[/] [dim]({profile})[/]")
    else:
        table.add_row("Claude login", f"[yellow]not found in {profile}: run `claude` and /login, or `claude setup-token`[/]")
    console.print(table)

    if check_claude:
        from media_analysis.providers.llm import Text, create_llm

        try:
            llm = create_llm(settings.llm.backend, settings.llm.model, settings.llm.effort, settings.llm.claude_profile)
            answer = llm.generate_json(
                "Reply via the required output format.", [Text("Say OK.")],
                {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]},
            )
            model = llm.model or "account default"
            console.print(f"Claude ({settings.llm.backend}, {model}): [green]{answer.get('reply')}[/]")
        except MediaAnalysisError as e:
            ok = False
            console.print(f"Claude: [red]{e}[/]")
    raise typer.Exit(0 if ok else 1)


@templates_app.command("list")
def templates_list(
    templates_dir: Annotated[Optional[list[Path]], typer.Option(help="Extra folder(s) with user templates.")] = None,
) -> None:
    """Show available templates (built-in and user)."""
    from media_analysis.workflows.report.templates.registry import load_templates

    try:
        templates = load_templates([*(templates_dir or []), USER_TEMPLATES_DIR])
    except MediaAnalysisError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    table = Table("id", "name", "description", "source")
    table.columns[0].no_wrap = True
    for t in templates.values():
        table.add_row(t.id, t.name, t.description, t.source)
    console.print(table)
    console.print(f"[dim]User templates folder: {USER_TEMPLATES_DIR}[/]")


@templates_app.command("new")
def templates_new(
    template_id: Annotated[str, typer.Argument(help="Id for the new template, e.g. sales-call.")],
    directory: Annotated[Path, typer.Option("--dir", help="Where to create it.")] = USER_TEMPLATES_DIR,
) -> None:
    """Create a template file to fill in."""
    from media_analysis.workflows.report.templates.registry import new_template

    try:
        path = new_template(template_id, directory)
    except MediaAnalysisError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    console.print(f"Created {path}\nEdit it, then rerun with --template {template_id}.")


def run_cli() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    app()
