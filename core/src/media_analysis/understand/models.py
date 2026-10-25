"""What the understanding stages find in the input files. Each result is cached as JSON."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class MediaFile(BaseModel):
    id: str
    """Short, stable id used in prompts and file names (e.g. 'f1')."""
    path: str
    name: str
    fingerprint: str
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    speaker: str | None = None
    """Who this file's audio is, for files recorded together on one timeline (e.g. "Me", "Others")."""
    duplicate_audio: bool = False
    """The sound is also in files recorded together with this one (a Meeting Recorder video), so it isn't used."""

    @property
    def uses_audio(self) -> bool:
        return self.has_audio and not self.duplicate_audio


class Ingest(BaseModel):
    files: list[MediaFile]
    skipped: list[str] = []

    def by_id(self, file_id: str) -> MediaFile:
        return next(f for f in self.files if f.id == file_id)


class Word(BaseModel):
    start: float
    end: float
    text: str


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] | None = None
    """Per-word times, only when word timestamps were asked for."""


class FileTranscript(BaseModel):
    file_id: str
    language: str | None
    segments: list[TranscriptSegment]


class Transcript(BaseModel):
    files: list[FileTranscript]


class VisualSegment(BaseModel):
    file_id: str
    index: int
    start: float
    end: float
    kind: Literal["static", "moving"]
    edge_density: float
    """Share of edge pixels; high for slides, code, diagrams."""
    rep_time: float
    """Time of the sharpest frame in the segment."""


class VisualScan(BaseModel):
    segments: list[VisualSegment]


class Shot(BaseModel):
    """One shot of camera footage (media/shots.py)."""

    file_id: str
    index: int
    start: float
    end: float
    sharpness: float
    shake: float
    brightness: float
    clipped: float
    rep_time: float


class ShotList(BaseModel):
    shots: list[Shot]


class FileSound(BaseModel):
    file_id: str
    loudness: float | None
    """Integrated loudness in LUFS; None when there is no measurable sound."""
    silences: list[tuple[float, float]]


class Sound(BaseModel):
    files: list[FileSound]


class ShotDescription(BaseModel):
    file_id: str
    index: int
    caption: str
    """What the shot shows, one sentence."""
    subjects: list[str] = []
    place: str = ""
    action: str = ""
    tags: list[str] = []
    issues: list[str] = []
    """Problems visible in the shot (blurry, too dark, obstructed, ...)."""


class Descriptions(BaseModel):
    shots: list[ShotDescription]
