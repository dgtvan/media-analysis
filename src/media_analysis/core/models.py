"""Artifacts passed between pipeline stages. Each one is cached as JSON."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


def format_timecode(seconds: float, sep: str = ":") -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{sep}{m:02d}{sep}{s:02d}"


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
        lines: list[str] = []
        for ft in self.files:
            mf = ingest.by_id(ft.file_id)
            lines.append(f"## {mf.id}: {mf.name} (duration {format_timecode(mf.duration)})")
            if not ft.segments:
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


class Classification(BaseModel):
    template_id: str | None
    confidence: float
    reason: str
    closest: list[str] = []
    suggested_template: str | None = None
    forced: bool = False


class EvidenceRequest(BaseModel):
    file_id: str
    start: float
    end: float
    reason: str
    look_for: str
    priority: int = 2


class FramePlan(BaseModel):
    requests: list[EvidenceRequest]


class FrameCandidate(BaseModel):
    id: str
    file_id: str
    time: float
    request_index: int
    path: str
    sharpness: float


class Candidates(BaseModel):
    candidates: list[FrameCandidate]


class EvidenceItem(BaseModel):
    id: str
    file_id: str
    time: float
    path: str
    caption: str
    description: str
    text: str


class Evidence(BaseModel):
    items: list[EvidenceItem]


class ReportDraft(BaseModel):
    title: str
    markdown: str
