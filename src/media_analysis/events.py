"""Progress events emitted by the pipeline, so any front end (CLI now, UI later) can render them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class EventKind(str, Enum):
    STAGE_STARTED = "stage_started"
    STAGE_SKIPPED = "stage_skipped"
    PROGRESS = "progress"
    ACTIVITY = "activity"
    """Live status of a step that has no measurable progress, e.g. a Claude call ("thinking", "writing")."""
    STAGE_DONE = "stage_done"
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    stage: str
    message: str = ""
    completed: float | None = None
    total: float | None = None

    def to_dict(self) -> dict:
        d = {"event": self.kind.value, "stage": self.stage, "message": self.message}
        if self.completed is not None:
            d["completed"], d["total"] = self.completed, self.total
        return d


EventSink = Callable[[Event], None]


def null_sink(_: Event) -> None:
    pass


class Reporter:
    """Small helper bound to one stage, so stage code doesn't build Event objects by hand."""

    def __init__(self, sink: EventSink, stage: str) -> None:
        self._sink = sink
        self.stage = stage

    def started(self, message: str = "") -> None:
        self._sink(Event(EventKind.STAGE_STARTED, self.stage, message))

    def skipped(self, message: str = "cached") -> None:
        self._sink(Event(EventKind.STAGE_SKIPPED, self.stage, message))

    def progress(self, completed: float, total: float, message: str = "") -> None:
        self._sink(Event(EventKind.PROGRESS, self.stage, message, completed, total))

    def done(self, message: str = "") -> None:
        self._sink(Event(EventKind.STAGE_DONE, self.stage, message))

    def activity(self, message: str) -> None:
        self._sink(Event(EventKind.ACTIVITY, self.stage, message))

    def info(self, message: str) -> None:
        self._sink(Event(EventKind.INFO, self.stage, message))

    def warning(self, message: str) -> None:
        self._sink(Event(EventKind.WARNING, self.stage, message))
