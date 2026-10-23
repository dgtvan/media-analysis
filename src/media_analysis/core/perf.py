"""Run performance: how long each stage took and whether it ran or came from the cache, reported at the end of every
run (also a failed one) so slow steps can be found and improved."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from media_analysis.core.models import format_timecode
from media_analysis.events import Event, EventKind, EventSink


@dataclass
class StageRecord:
    stage: str
    result: str = "ran"
    """ran, cached, skipped (with the reason) or failed."""
    seconds: float = 0.0
    message: str = ""
    _started: float | None = field(default=None, repr=False)


class StageTimer:
    """An event sink that passes events through and records stage timings from them."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._run_started = time.monotonic()
        self.stages: dict[str, StageRecord] = {}
        self.claude_calls = 0
        self.claude_seconds = 0.0
        self.audio_seconds = 0.0

    def __call__(self, e: Event) -> None:
        now = time.monotonic()
        rec = self.stages.setdefault(e.stage, StageRecord(e.stage)) if e.stage else None
        if rec is not None:
            if e.kind == EventKind.STAGE_STARTED:
                rec._started = now
            elif e.kind in (EventKind.STAGE_DONE, EventKind.STAGE_SKIPPED):
                if rec._started is not None:
                    rec.seconds = now - rec._started
                    rec._started = None
                if e.kind == EventKind.STAGE_SKIPPED:
                    rec.result = "cached" if e.message == "cached" else "skipped"
                else:
                    rec.result = "ran"
                rec.message = e.message
        self._sink(e)

    def add_claude_call(self, seconds: float) -> None:
        self.claude_calls += 1
        self.claude_seconds += seconds

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
            lines.append(f"  Claude: {self.claude_calls} call(s), {format_timecode(self.claude_seconds)} in total")
        data = {
            "failed": failed,
            "total_seconds": round(total, 1),
            "claude_calls": self.claude_calls,
            "claude_seconds": round(self.claude_seconds, 1),
            "audio_seconds": round(self.audio_seconds, 1),
            "transcribe_speed": round(speed, 2) if speed else None,
            "stages": [{"stage": r.stage, "result": r.result, "seconds": round(r.seconds, 1), "message": r.message} for r in rows],
        }
        return Event(EventKind.SUMMARY, "summary", "\n".join(lines), data=data)
