"""The report workflow: a folder of recordings of one session → report.md written to a template, plus reference/.

ingest → transcribe → scan (understanding, shared) → classify → plan → extract → read → report → assemble
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.events import EventSink, null_sink
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import RunContext, RunOptions, Workflow
from media_analysis.providers.llm import create_llm
from media_analysis.understand.ingest import IngestStage
from media_analysis.understand.models import Ingest
from media_analysis.understand.scan import ScanStage
from media_analysis.understand.transcribe import TranscribeStage
from media_analysis.workflows.report.assemble import AssembleStage
from media_analysis.workflows.report.classify import ClassifyStage
from media_analysis.workflows.report.extract import ExtractStage
from media_analysis.workflows.report.models import Classification
from media_analysis.workflows.report.plan import PlanStage
from media_analysis.workflows.report.read import ReadStage
from media_analysis.workflows.report.templates.registry import Template, load_templates
from media_analysis.workflows.report.write import WriteStage


@dataclass
class ReportOptions(RunOptions):
    template_id: str | None = None
    """Use this template instead of detecting one."""


@dataclass
class ReportResult:
    report_path: Path
    classification: Classification
    evidence_count: int
    warnings: list[str] = field(default_factory=list)


class ReportWorkflow(Workflow[ReportOptions, ReportResult]):
    name = "report"
    stages = [
        IngestStage, TranscribeStage, ScanStage, ClassifyStage, PlanStage, ExtractStage, ReadStage, WriteStage,
        AssembleStage,
    ]

    def __init__(self, settings: Settings, sink: EventSink = null_sink) -> None:
        super().__init__(settings, sink)
        self._templates: dict[str, Template] = {}
        # Kept for the run statistics, which are also written when a later stage fails.
        self._ingest: Ingest | None = None
        self._classification: Classification | None = None

    def prepare(self, opts: ReportOptions) -> None:
        self._templates = load_templates(self.settings.templates_dirs)
        if opts.template_id and opts.template_id not in self._templates:
            raise MediaAnalysisError(
                f"Unknown template '{opts.template_id}'. Available: {', '.join(sorted(self._templates))}."
            )

    def create_llm(self) -> Any:
        llm = self.settings.llm
        return create_llm(llm.backend, llm.model, llm.effort, llm.claude_profile)

    def execute(self, ctx: RunContext, opts: ReportOptions) -> ReportResult:
        templates = self._templates
        ingest = self._ingest = IngestStage(ctx).run(opts.input_dir)
        ctx.timer.audio_seconds = sum(f.duration for f in ingest.files if f.has_audio)
        transcript = TranscribeStage(ctx).run(ingest)
        scan = ScanStage(ctx).run(ingest)
        classification = self._classification = ClassifyStage(ctx).run(
            ingest, transcript, scan, templates, opts.template_id,
        )
        template = templates[classification.template_id]  # type: ignore[index]
        plan = PlanStage(ctx).run(ingest, transcript, scan, template)
        candidates = ExtractStage(ctx).run(ingest, plan)
        evidence = ReadStage(ctx).run(ingest, plan, candidates, template)
        draft = WriteStage(ctx).run(ingest, transcript, evidence, template)
        report_path, warnings = AssembleStage(ctx).run(opts.output_dir, draft, evidence, ingest, template, classification)
        return ReportResult(report_path, classification, len(evidence.items), warnings)

    def record_fields(self, result: ReportResult | None) -> tuple[dict[str, Any], dict[str, Any]]:
        files = self._ingest.files if self._ingest else []
        c = self._classification
        fields = {
            "media": {
                "files": [
                    {"name": f.name, "fingerprint": f.fingerprint, "seconds": round(f.duration, 1),
                     "has_video": f.has_video, "has_audio": f.has_audio, "speaker": f.speaker}
                    for f in files
                ],
                "video_seconds": round(sum(f.duration for f in files if f.has_video), 1),
                "audio_seconds": round(self.timer.audio_seconds, 1),
            },
            "template": None if c is None else {
                "id": c.template_id, "generic": c.fallback, "forced": c.forced, "confidence": c.confidence,
            },
            "evidence_count": result.evidence_count if result else None,
        }
        return fields, {"max_frames": self.settings.evidence.max_requests}
