"""What the report stages produce. Each result is cached as JSON."""

from __future__ import annotations

from pydantic import BaseModel


class SuggestedTemplate(BaseModel):
    """A template Claude proposes when none of the existing ones fits."""

    id: str
    name: str
    description: str
    signals: list[str] = []
    evidence: list[str] = []
    sections: list[str] = []


class Classification(BaseModel):
    template_id: str | None
    """The template the report uses."""
    confidence: float
    reason: str
    closest: list[str] = []
    mismatch: list[str] = []
    """When no template fits: key points why the closest templates don't fit."""
    suggested: SuggestedTemplate | None = None
    fallback: bool = False
    """True when no template fit and the generic template was used."""
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
