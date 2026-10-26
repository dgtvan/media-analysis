"""How an analysis was made, as Markdown in the output's working folder (.cache): the settings that shaped it and,
step by step, whether the step ran or reused an earlier result and what did the work (transcription engine, Claude
model). Kept out of the deliverable, which is only the report and its references."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from media_analysis import __version__
from media_analysis.engine.perf import StageRecord, StageTimer
from media_analysis.engine.settings import Settings

FILE_NAME = "how-made.md"

STEP_NAMES = {
    "ingest": "Read the files", "transcribe": "Transcribe speech", "correct": "Correct misheard words", "scan": "Scan the video", "condense": "Condense the transcript",
    "classify": "Choose the template", "plan": "Plan key moments", "extract": "Extract frames", "read": "Read the frames",
    "report": "Write the report", "assemble": "Write the output folder", "shots": "Find shots", "sound": "Measure sound",
    "describe": "Describe shots", "prepare": "Make previews", "sequence": "Plan the edit",
}


def how_made(title: str, timer: StageTimer, settings: Settings, started: datetime, extras: list[tuple[str, str]]) -> str:
    """The Markdown document. `extras` are (label, text) lines the workflow adds, e.g. the template it used."""
    llm = settings.llm
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    claude = "subscription login" if llm.backend == "subscription" else "API key"
    if llm.backend == "subscription":
        claude += f" (profile {llm.profile_dir()})"
    claude += f"; model: {llm.model or 'account default'}; thinking effort: {llm.effort or 'model default'}"
    lines = [
        f"# {title}",
        "",
        f"- **Run:** {started.strftime('%Y-%m-%d %H:%M UTC')}, {_duration(elapsed)} in total, media-analysis {__version__}",
    ]
    if settings.resource_use:
        lines.append(f"- **Resource use:** {settings.resource_use} (at the start)")
    lines.append(f"- **Claude:** {claude}")
    lines += [f"- **{label}:** {text}" for label, text in extras]
    lines += ["", "| Step | This run | Made with |", "|---|---|---|"]
    for rec in timer.stages.values():
        if rec.stage == "summary":
            continue
        name = STEP_NAMES.get(rec.stage, rec.stage.capitalize())
        lines.append(f"| {name} | {_this_run(rec, timer)} | {_made_with(rec)} |")
    lines += ["", "Reused results were made by an earlier analysis of the same files with the same settings."]
    return "\n".join(lines) + "\n"


def _this_run(rec: StageRecord, timer: StageTimer) -> str:
    # Some stages report a cache hit as done; one that only reused results did no work of its own.
    if rec.result == "cached" or (rec.reused and not rec.made_with and not rec.claude_calls):
        return "reused"
    if rec.result == "skipped":
        return f"skipped: {rec.message}" if rec.message else "skipped"
    text = f"{rec.result}, {_duration(rec.seconds)}"
    if rec.stage == "transcribe" and rec.result == "ran" and rec.seconds > 0 and timer.audio_seconds:
        text += f" for {_duration(timer.audio_seconds)} of audio ({timer.audio_seconds / rec.seconds:.1f}x real time)"
    if rec.claude_calls:
        text += f", {rec.claude_calls} Claude call{'s' if rec.claude_calls > 1 else ''}"
    return text


def _made_with(rec: StageRecord) -> str:
    parts = []
    now = _describe({**rec.made_with, "claude_models": sorted(rec.claude_models)})
    if now:
        parts.append(now)
    elif rec.result == "ran" and not rec.reused:
        parts.append("this computer")
    for earlier in rec.reused:
        made_at = earlier.get("made_at")
        when = f"earlier ({_date(made_at)})" if made_at else "earlier (details not kept)"
        described = _describe(earlier)
        parts.append(f"{when}: {described}" if described else when)
    return "; ".join(parts).replace("|", "\\|")


def _describe(made_with: dict[str, Any]) -> str:
    bits = []
    if made_with.get("transcriber"):
        bits.append(str(made_with["transcriber"]))
    if made_with.get("claude_models"):
        bits.append("Claude " + ", ".join(made_with["claude_models"]))
    return ", ".join(bits)


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds // 60 % 60} min"
