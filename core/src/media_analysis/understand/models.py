"""What the understanding stages find in the input files. Each result is cached as JSON."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from media_analysis.engine.timecode import format_timecode


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


class Ingest(BaseModel):
    files: list[MediaFile]
    skipped: list[str] = []

    def by_id(self, file_id: str) -> MediaFile:
        return next(f for f in self.files if f.id == file_id)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class FileTranscript(BaseModel):
    file_id: str
    language: str | None
    segments: list[TranscriptSegment]


class Transcript(BaseModel):
    files: list[FileTranscript]

    def to_prompt_text(self, ingest: Ingest) -> str:
        """Timecoded transcript per file. Files with a speaker were recorded together, so their speech is merged
        into one conversation on a shared timeline, each line labelled with its speaker."""
        lines: list[str] = []
        together = [ft for ft in self.files if ingest.by_id(ft.file_id).speaker]
        if together:
            sources = ", ".join(
                f"{mf.id} ({mf.name}) = {mf.speaker}" for mf in (ingest.by_id(ft.file_id) for ft in together)
            )
            lines.append(f"## Conversation: {sources}")
            lines.append(
                "These files were recorded at the same time, together with any silent video below, so all times are on "
                "one shared timeline. \"Me\" is the person who made the recording (microphone); \"Others\" is everyone "
                "heard through the computer."
            )
            merged = sorted(
                (seg.start, ingest.by_id(ft.file_id).speaker, seg.text.strip()) for ft in together for seg in ft.segments
            )
            if not merged:
                lines.append("(no speech)")
            lines += [f"[{format_timecode(start)}] {speaker}: {text}" for start, speaker, text in merged]
            lines.append("")
        for ft in self.files:
            mf = ingest.by_id(ft.file_id)
            if mf.speaker:
                continue
            lines.append(f"## {mf.id}: {mf.name} (duration {format_timecode(mf.duration)})")
            if not mf.has_audio:
                lines.append("(no audio)")
            elif not ft.segments:
                lines.append("(no speech)")
            for seg in ft.segments:
                lines.append(f"[{format_timecode(seg.start)}] {seg.text.strip()}")
            lines.append("")
        return "\n".join(lines)


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

    def to_prompt_text(self) -> str:
        if not self.segments:
            return "(no video)"
        lines = ["file | # | start-end | kind | edge density"]
        for s in self.segments:
            lines.append(
                f"{s.file_id} | {s.index} | {format_timecode(s.start)}-{format_timecode(s.end)} | {s.kind} | {s.edge_density:.3f}"
            )
        return "\n".join(lines)
