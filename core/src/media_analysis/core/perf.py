"""Run performance: how long each stage took, whether it ran or came from the cache, and the Claude calls and tokens it
used, reported at the end of every run (also a failed one) so slow or costly steps can be found and improved."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_analysis import __version__
from media_analysis.config import Settings
from media_analysis.core.models import Classification, MediaFile, format_timecode
from media_analysis.events import Event, EventKind, EventSink
from media_analysis.providers.llm import Usage


@dataclass
class StageRecord:
    stage: str
    result: str = "ran"
    """ran, cached, skipped (with the reason) or failed."""
    seconds: float = 0.0
    message: str = ""
    claude_calls: int = 0
    claude_seconds: float = 0.0
    usage: Usage = field(default_factory=Usage)
    _started: float | None = field(default=None, repr=False)


class StageTimer:
    """An event sink that passes events through and records stage timings from them."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._run_started = time.monotonic()
        self.stages: dict[str, StageRecord] = {}
        self.claude_calls = 0
        self.claude_seconds = 0.0
        self.usage = Usage()
        self.audio_seconds = 0.0
        self._current: str | None = None

    def __call__(self, e: Event) -> None:
        now = time.monotonic()
        rec = self.stages.setdefault(e.stage, StageRecord(e.stage)) if e.stage else None
        if rec is not None:
            if e.kind == EventKind.STAGE_STARTED:
                rec._started = now
                self._current = e.stage
            elif e.kind in (EventKind.STAGE_DONE, EventKind.STAGE_SKIPPED):
                if rec._started is not None:
                    rec.seconds = now - rec._started
                    rec._started = None
                if e.kind == EventKind.STAGE_SKIPPED:
                    rec.result = "cached" if e.message == "cached" else "skipped"
                else:
                    rec.result = "ran"
                rec.message = e.message
                if self._current == e.stage:
                    self._current = None
        self._sink(e)

    def add_claude_call(self, seconds: float, usage: Usage | None = None) -> None:
        """Counts a Claude call towards the run and the stage in progress, and logs it."""
        self.claude_calls += 1
        self.claude_seconds += seconds
        stage = self._current or ""
        rec = self.stages.get(stage) if stage else None
        if rec is not None:
            rec.claude_calls += 1
            rec.claude_seconds += seconds
        if usage is not None:
            self.usage += usage
            if rec is not None:
                rec.usage += usage
        detail = usage.describe() if usage is not None else "tokens not reported"
        self._sink(Event(EventKind.INFO, stage, f"Claude call {self.claude_calls}: {format_timecode(seconds)}, {detail}",
                         data={"claude_call": {"seconds": round(seconds, 1), **(usage.to_dict() if usage else {})}}))

    def summary(self, failed: bool) -> Event:
        now = time.monotonic()
        for rec in self.stages.values():
            if rec._started is not None:
                rec.seconds = now - rec._started
                rec.result = "failed" if failed else rec.result
                rec._started = None
        total = now - self._run_started
        rows = [r for r in self.stages.values() if r.stage != "summary"]
        transcribe = self.stages.get("transcribe")
        speed = None
        if transcribe and transcribe.result == "ran" and transcribe.seconds > 0 and self.audio_seconds:
            speed = self.audio_seconds / transcribe.seconds

        lines = ["Performance", f"  {'stage':<11} {'result':<8} {'time':>8}  details"]
        for r in rows:
            detail = r.message if r.result != "ran" or r.stage != "transcribe" or speed is None else (
                f"{r.message}; {format_timecode(self.audio_seconds)} of audio at {speed:.2f}x real time"
            )
            lines.append(f"  {r.stage:<11} {r.result:<8} {format_timecode(r.seconds):>8}  {detail}".rstrip())
        lines.append(f"  {'total':<11} {'failed' if failed else 'done':<8} {format_timecode(total):>8}")
        if self.claude_calls:
            lines.append(f"  Claude: {self.claude_calls} call(s), {format_timecode(self.claude_seconds)}, {self.usage.describe()}")
            for r in rows:
                if r.claude_calls:
                    calls = f"{r.claude_calls} call{'s' if r.claude_calls > 1 else ''}"
                    lines.append(f"    {r.stage:<11} {calls:<8} {format_timecode(r.claude_seconds):>8}  {r.usage.describe()}")
        data = {
            "failed": failed,
            "total_seconds": round(total, 1),
            "claude_calls": self.claude_calls,
            "claude_seconds": round(self.claude_seconds, 1),
            "claude_usage": self.usage.to_dict(),
            "audio_seconds": round(self.audio_seconds, 1),
            "transcribe_speed": round(speed, 2) if speed else None,
            "stages": [
                {"stage": r.stage, "result": r.result, "seconds": round(r.seconds, 1), "message": r.message,
                 "claude_calls": r.claude_calls, "claude_usage": r.usage.to_dict()}
                for r in rows
            ],
        }
        return Event(EventKind.SUMMARY, "summary", "\n".join(lines), data=data)


RUN_RECORD_SCHEMA = 1


def run_record(
    *,
    started: datetime,
    input_dir: Path,
    output_dir: Path,
    summary: Event,
    settings: Settings,
    files: list[MediaFile],
    classification: Classification | None,
    evidence_count: int | None,
    error: str | None,
    project_name: str | None = None,
    log_file: Path | None = None,
    fresh: bool = False,
    force_from: str | None = None,
) -> dict[str, Any]:
    """One analysis run as a flat, stable JSON object for statistics (one line per run in the stats file)."""
    data = summary.data or {}
    llm = settings.llm
    return {
        "schema": RUN_RECORD_SCHEMA,
        "run_id": uuid.uuid4().hex,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outcome": "failed" if data.get("failed") else "succeeded",
        "error": error,
        "app_version": __version__,
        "project": {
            "name": input_dir.name, "display_name": project_name or input_dir.name,
            "input_dir": str(input_dir), "output_dir": str(output_dir),
        },
        "log_file": str(log_file) if log_file else None,
        "fresh": fresh,
        "force_from": force_from,
        "media": {
            "files": [
                {"name": f.name, "fingerprint": f.fingerprint, "seconds": round(f.duration, 1), "has_video": f.has_video,
                 "has_audio": f.has_audio, "speaker": f.speaker}
                for f in files
            ],
            "video_seconds": round(sum(f.duration for f in files if f.has_video), 1),
            "audio_seconds": data.get("audio_seconds"),
        },
        "template": None if classification is None else {
            "id": classification.template_id, "generic": classification.fallback, "forced": classification.forced,
            "confidence": classification.confidence,
        },
        "evidence_count": evidence_count,
        "settings": {
            "backend": llm.backend, "model": llm.model, "effort": llm.effort,
            "claude_profile": str(llm.profile_dir()) if llm.backend == "subscription" else None,
            "whisper_model": settings.transcription.model, "device": settings.transcription.device,
            "language": settings.transcription.language, "max_frames": settings.evidence.max_requests,
        },
        "total_seconds": data.get("total_seconds"),
        "transcribe_speed": data.get("transcribe_speed"),
        "claude": {"calls": data.get("claude_calls", 0), "seconds": data.get("claude_seconds", 0.0), **(data.get("claude_usage") or {})},
        "stages": data.get("stages", []),
    }


def append_run_record(path: Path, record: dict[str, Any]) -> None:
    """Appends the record as one JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
