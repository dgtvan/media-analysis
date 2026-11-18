"""Stage 'report': one Claude call writes the report in Markdown from the template, transcript and evidence."""

from __future__ import annotations

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.providers.llm import Text
from media_analysis.understand.models import Ingest
from media_analysis.workflows.report import prompts
from media_analysis.workflows.report.models import Evidence, ReportDraft
from media_analysis.workflows.report.templates.registry import Template


class WriteStage(Stage):
    name = "report"
    # 2: reads the phrase view of the transcript (understand/views.py). 3: cites one time per moment.
    # 4: room microphones and one-sided recordings. 5: Conclusion first, the template's sections under Details.
    version = 5

    def run(self, ingest: Ingest, transcript: str, evidence: Evidence, template: Template) -> ReportDraft:
        rep = self.rep
        key = self.key(transcript, evidence, template.fingerprint_parts(), self.settings.llm.model, self.settings.llm.effort)
        cached = None if self.force else self.cache.load(self.name, key, ReportDraft)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started(f"writing the {template.name.lower()} report")
        template_text = (
            f"Template: {template.name} — {template.description}\n"
            "Detail sections (H3 under ## Details, in this order):\n" + "\n".join(f"- {s}" for s in template.sections)
            + f"\n\nInstructions:\n{template.instructions}"
        )
        files_text = "\n".join(
            f"- {f.id}: {f.name} ({'video' if f.has_video else 'audio'}, {format_timecode(f.duration)})"
            for f in ingest.files
        )
        if evidence.items:
            ev_text = "\n\n".join(
                f"## {e.id} — {e.file_id} at {format_timecode(ingest.by_id(e.file_id).clock(e.time))}\nCaption: {e.caption}\n"
                f"Description: {e.description}\nText in frame:\n{e.text or '(none)'}"
                for e in evidence.items
            )
        else:
            ev_text = "(no evidence frames)"
        content = [
            Text("# Template\n" + template_text),
            Text("# Files\n" + files_text),
            Text("# Evidence frames\n" + ev_text),
            Text("# Transcript\n" + transcript),
        ]
        markdown = self.ctx.llm.generate_text(prompts.REPORT_SYSTEM, content, rep.activity).strip()
        if not markdown:
            raise MediaAnalysisError("Claude returned an empty report.")
        first = markdown.splitlines()[0]
        title = first[2:].strip() if first.startswith("# ") else template.name
        draft = ReportDraft(title=title, markdown=markdown)
        self.cache.save(self.name, key, draft)
        rep.done()
        return draft
