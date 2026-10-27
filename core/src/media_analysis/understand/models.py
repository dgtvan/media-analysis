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
    silent: bool = False
    """The recorder measured no sound in it (Meeting Recorder's recording.json), so it isn't transcribed."""
    offset: float = 0.0
    """Where the file starts on the shared clock of files recorded together, in seconds (e.g. a video that started
    1.7 s after the audio). Views show times on that clock; media tools take times in the file."""

    @property
    def uses_audio(self) -> bool:
        return self.has_audio and not self.duplicate_audio and not self.silent

    def clock(self, t: float) -> float:
        """A time in this file, on the shared clock."""
        return t + self.offset


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
    language: str | None = None
    """ISO code of the segment's language when the engine gives one per segment or chunk (None in older caches)."""


class FileTranscript(BaseModel):
    file_id: str
    language: str | None
    segments: list[TranscriptSegment]

    def speech_by_language(self) -> dict[str, float]:
        """Seconds of speech per language; segments without their own language count as the file's."""
        seconds: dict[str, float] = {}
        for s in self.segments:
            if lang := s.language or self.language:
                seconds[lang] = seconds.get(lang, 0.0) + max(0.0, s.end - s.start)
        return seconds


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

