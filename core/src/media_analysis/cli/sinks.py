"""Renders run events: a live console view, or JSON Lines for programs driving the core."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TaskID, TaskProgressColumn, TextColumn, TimeElapsedColumn,
)

from media_analysis.engine.events import Event, EventKind


class ConsoleSink:
    """One line per stage, plus a live line (spinner, status, progress bar, elapsed time) while a stage runs."""

    def __init__(self, console: Console, verbose: bool) -> None:
        self.console = console
        self.verbose = verbose
        self.live: Progress | None = None
        self.task: TaskID | None = None

    def _ensure_live(self, description: str) -> Progress:
        if self.live is None:
            self.live = Progress(
                SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(),
                TimeElapsedColumn(), console=self.console, transient=True,
            )
            self.live.start()
            self.task = self.live.add_task(description, total=None)
        return self.live

    def _close_live(self) -> None:
        if self.live:
            self.live.stop()
            self.live = None

    def __call__(self, e: Event) -> None:
        if e.kind == EventKind.PROGRESS:
            live = self._ensure_live(e.message or e.stage)
            extra = {"description": e.message} if e.message else {}
            live.update(self.task, completed=e.completed, total=e.total, **extra)
        elif e.kind == EventKind.ACTIVITY:
            self._ensure_live(e.message).update(self.task, description=e.message)
        elif e.kind == EventKind.STAGE_STARTED:
            self._close_live()
            self.console.print(f"[bold cyan]▶ {e.stage}[/] {e.message}")
            self._ensure_live(e.message or e.stage)
        elif e.kind in (EventKind.STAGE_DONE, EventKind.STAGE_SKIPPED):
            self._close_live()
            if e.kind == EventKind.STAGE_DONE:
                self.console.print(f"[green]✓ {e.stage}[/] {e.message}".rstrip())
            else:
                self.console.print(f"[dim]✓ {e.stage}: {e.message}[/]")
        elif e.kind == EventKind.WARNING:
            self.console.print(f"[yellow]  ! {e.message}[/]")
        elif e.kind == EventKind.SUMMARY:
            self._close_live()
            self.console.print(f"\n[dim]{e.message}[/]", highlight=False)
        elif e.kind == EventKind.INFO and self.verbose:
            self.console.print(f"[dim]  {e.message}[/]")

    def close(self) -> None:
        self._close_live()


class JsonSink:
    """JSON Lines on stdout, one object per event, for scripts and other programs driving the app."""

    def __init__(self) -> None:
        self.started = time.monotonic()

    def emit(self, data: dict) -> None:
        data = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed": round(time.monotonic() - self.started, 1),
            **data,
        }
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def __call__(self, e: Event) -> None:
        self.emit(e.to_dict())

    def close(self) -> None:
        pass
