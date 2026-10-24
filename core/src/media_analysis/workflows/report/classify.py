"""Stage 'classify': one Claude call picks the report template that fits; the generic template when none does."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.cache import digest
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.media import ffmpeg
from media_analysis.media.frames import contact_sheet
from media_analysis.providers.llm import Image, Text
from media_analysis.understand.models import Ingest, Transcript, VisualScan
from media_analysis.workflows.report import prompts
from media_analysis.workflows.report.models import Classification, SuggestedTemplate
from media_analysis.workflows.report.templates.registry import Template


class ClassifyStage(Stage):
    name = "classify"
    version = 2

    def run(
        self, ingest: Ingest, transcript: Transcript, scan: VisualScan, templates: dict[str, Template],
        forced_id: str | None,
    ) -> Classification:
        rep = self.rep
        if forced_id:
            rep.skipped(f"template forced: {forced_id}")
            return Classification(template_id=forced_id, confidence=1.0, reason="Template selected by the user.", forced=True)
        fallback = next((t for t in templates.values() if t.fallback), None)
        if fallback is None:
            raise MediaAnalysisError("No fallback template found (the built-in 'generic' template is missing).")
        templates = {tid: t for tid, t in templates.items() if not t.fallback}
        llm_settings = self.settings.llm
        key = self.key(
            transcript, scan, [t.fingerprint_parts() for t in templates.values()], llm_settings.model, llm_settings.effort,
        )
        result = None if self.force else self.cache.load(self.name, key, Classification)
        if result is None:
            rep.started("asking Claude which template fits")
            content: list = [
                Text("# Available templates\n" + "\n".join(t.summary_for_classification() for t in templates.values())),
                Text("# Transcript\n" + transcript.to_prompt_text(ingest)),
                Text("# Visual segments\n" + scan.to_prompt_text()),
            ]
            sheet = self._contact_sheet(ingest, scan)
            if sheet:
                content += [Text("# Contact sheet (thumbnails labelled with file id and time)"), Image(sheet)]
            data = self.ctx.llm.generate_json(
                prompts.CLASSIFY_SYSTEM, content, prompts.classify_schema(list(templates)), rep.activity,
            )
            tid = data.get("template_id")
            suggested = data.get("suggested_template")
            result = Classification(
                template_id=tid if tid in templates else None,
                confidence=float(data.get("confidence", 0)),
                reason=data.get("reason", ""),
                closest=[c for c in data.get("closest", []) if c in templates],
                mismatch=[m for m in data.get("mismatch", []) if m],
                suggested=SuggestedTemplate(**suggested) if isinstance(suggested, dict) and suggested.get("id") else None,
            )
            self.cache.save(self.name, key, result)
            cached_note = ""
        else:
            cached_note = ", cached"
        if result.template_id is None or result.confidence < llm_settings.classify_min_confidence:
            # A weak best guess is still the closest match.
            closest = [result.template_id] if result.template_id else []
            closest += [c for c in result.closest if c not in closest]
            result = result.model_copy(update={"template_id": fallback.id, "closest": closest[:3], "fallback": True})
            rep.warning(f"no template fits: {result.reason}")
            rep.done(f"no template fits, writing a {fallback.name.lower()} report{cached_note}")
            return result
        rep.done(f"{result.template_id} (confidence {result.confidence:.2f}{cached_note})")
        return result

    def _contact_sheet(self, ingest: Ingest, scan: VisualScan, limit: int = 16) -> Path | None:
        if not scan.segments:
            return None
        chosen = sorted(scan.segments, key=lambda s: (s.kind != "static", -(s.end - s.start)))[:limit]
        chosen.sort(key=lambda s: (s.file_id, s.start))
        thumbs_dir = self.cache.dir("scan", "thumbs")
        items = []
        for s in chosen:
            f = ingest.by_id(s.file_id)
            out = thumbs_dir / f"{f.fingerprint}-{int(s.rep_time * 1000)}.jpg"
            if not out.exists():
                ffmpeg.extract_still(Path(f.path), s.rep_time, out, max_side=640)
            items.append((f"{s.file_id} {format_timecode(s.rep_time)}", out))
        return contact_sheet(items, self.cache.dir("scan") / f"sheet-{digest([str(p) for _, p in items])}.jpg")
