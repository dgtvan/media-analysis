"""The report workflow: a folder of recordings of one session → report.md written to a template, plus reference/.

ingest → transcribe → scan → condense (understanding, shared) → classify → plan → extract → read → report → assemble
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.events import EventSink, null_sink
from media_analysis.engine.how_made import FILE_NAME as HOW_MADE_FILE
from media_analysis.engine.how_made import how_made
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import CACHE_DIR_NAME, RunContext, RunOptions, Workflow
from media_analysis.providers.llm import create_llm
from media_analysis.understand.condense import CondenseStage
from media_analysis.understand.correct import CORRECTIONS_FILE, CorrectStage, corrections_markdown
from media_analysis.understand.ingest import IngestStage
from media_analysis.understand.languages import describe, language_shares
from media_analysis.understand.models import Ingest
from media_analysis.understand.scan import ScanStage
from media_analysis.understand.transcribe import TranscribeStage
from media_analysis.understand.views import transcript_view
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
    languages: dict[str, float] = field(default_factory=dict)
    """Share of the speech per language, largest first."""


class ReportWorkflow(Workflow[ReportOptions, ReportResult]):
    name = "report"
    stages = [
        IngestStage, TranscribeStage, CorrectStage, ScanStage, CondenseStage, ClassifyStage, PlanStage, ExtractStage, ReadStage, WriteStage,
        AssembleStage,
    ]

    def __init__(self, settings: Settings, sink: EventSink = null_sink) -> None:
        super().__init__(settings, sink)
        self._templates: dict[str, Template] = {}
        # Kept for the run statistics, which are also written when a later stage fails.
        self._ingest: Ingest | None = None
        self._classification: Classification | None = None
        self._languages: dict[str, float] = {}

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
        ctx.timer.audio_seconds = sum(f.duration for f in ingest.files if f.uses_audio)
        transcript = TranscribeStage(ctx).run(ingest)
        languages = self._languages = language_shares(transcript)
        # Working files for whoever wants to check how a report was made; the deliverable is report.md and reference/.
        working = opts.output_dir / CACHE_DIR_NAME
        for name in (HOW_MADE_FILE, CORRECTIONS_FILE):
            (opts.output_dir / name).unlink(missing_ok=True)  # where builds of 2026-10-26 put them
        corrections_path = working / CORRECTIONS_FILE
        corrections_path.unlink(missing_ok=True)
        if self.settings.correct_transcript:
            corrected = CorrectStage(ctx).run(ingest, transcript)
            transcript = corrected.transcript
            if corrected.corrections:
                corrections_path.write_text(corrections_markdown(ingest, corrected.corrections), encoding="utf-8")
        scan = ScanStage(ctx).run(ingest)
        text = CondenseStage(ctx).run(transcript_view(ingest, transcript)).text
        classification = self._classification = ClassifyStage(ctx).run(
            ingest, text, scan, templates, opts.template_id,
        )
        template = templates[classification.template_id]  # type: ignore[index]
        plan = PlanStage(ctx).run(ingest, text, scan, template)
        candidates = ExtractStage(ctx).run(ingest, plan)
        evidence = ReadStage(ctx).run(ingest, plan, candidates, template)
        draft = WriteStage(ctx).run(ingest, text, evidence, template)
        report_path, warnings = AssembleStage(ctx).run(opts.output_dir, draft, evidence, ingest, template, classification)
        if classification.forced:
            chosen = "selected by the user"
        elif classification.fallback:
            chosen = f"no template fit (closest confidence {classification.confidence:.2f}), so the generic one"
        else:
            chosen = f"chosen by Claude (confidence {classification.confidence:.2f})"
        extras = [
            ("Speech", describe(languages) or "none transcribed"),
            ("Template", f"{template.name} (`{template.id}`), {chosen}"),
            ("Key moments", f"up to {self.settings.evidence.max_requests}; {len(evidence.items)} frame(s) in the report"),
        ]
        (working / HOW_MADE_FILE).write_text(
            how_made("How this report was made", self.timer, self.settings, self.started, extras), encoding="utf-8")
        return ReportResult(report_path, classification, len(evidence.items), warnings, languages)

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
            "languages": self._languages or None,
        }
        return fields, {"max_frames": self.settings.evidence.max_requests}
