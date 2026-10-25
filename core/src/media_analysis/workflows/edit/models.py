"""The edit plan: a multi-track timeline built from the footage, stored as versioned JSON (engine/documents.py).

Every item has a stable id (for diffs between versions) and a short label people and Claude use to refer to it ("#3",
"T1", "FX2"). Times are whole milliseconds: `src_in`/`src_out` say which part of a clip is used, `at` where it sits in
the finished video.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TrackKind = Literal["video", "audio", "text", "music", "sfx"]


class Clip(BaseModel):
    """A part of one footage file on a video (or audio) track."""

    id: str
    label: str
    file_id: str
    src_in: int
    src_out: int
    at: int
    role: str = ""
    """hook, context, peak, b-roll, outro, ..."""
    reason: str = ""
    transcript: str = ""
    """What is said in the used part (empty for silent footage)."""
    muted: bool = False
    """B-roll over speech plays without its own sound."""
    anchor: str | None = None
    """For items laid over the main track: the main clip they belong to (its id); `offset` is from its start."""
    offset: int = 0

    @property
    def dur(self) -> int:
        return self.src_out - self.src_in


class Mark(BaseModel):
    """Text on screen, or a placeholder for music or a sound effect that has no file yet."""

    id: str
    label: str
    at: int
    dur: int
    text: str
    anchor: str | None = None
    """The main clip it belongs to (its id); `offset` is from that clip's start. None = fixed position."""
    offset: int = 0


class Track(BaseModel):
    id: str
    """V1, V2, T1, A1, A2, A3, ..."""
    name: str
    kind: TrackKind
    linked: str | None = None
    """For the dialogue track: the video track whose sound it is (its items are that track's clips)."""
    clips: list[Clip] = []
    marks: list[Mark] = []


class EditFormat(BaseModel):
    aspect: str = "16:9"
    fps: int = 30
    target_seconds: int = 60
    max_seconds: int | None = None
    """The user's limit for the edit's length; None = no limit."""


class EditPlan(BaseModel):
    schema_version: int = 1
    profile_id: str
    format: EditFormat = Field(default_factory=EditFormat)
    strategy: str = ""
    """The plan Claude followed, in a few sentences."""
    tracks: list[Track]

    def track(self, track_id: str) -> Track | None:
        return next((t for t in self.tracks if t.id == track_id), None)

    def items(self) -> dict[str, dict]:
        """{id: item} over all tracks, for diffs between versions."""
        out: dict[str, dict] = {}
        for t in self.tracks:
            for c in t.clips:
                out[c.id] = {"track": t.id, **c.model_dump()}
            for m in t.marks:
                out[m.id] = {"track": t.id, **m.model_dump()}
        return out

    def by_label(self, label: str) -> tuple[Track, Clip | Mark] | None:
        for t in self.tracks:
            for item in [*t.clips, *t.marks]:
                if item.label.lower() == label.strip().lower():
                    return t, item
        return None

    def duration(self) -> int:
        ends = [c.at + c.dur for t in self.tracks for c in t.clips] + [m.at + m.dur for t in self.tracks for m in t.marks]
        return max(ends, default=0)
