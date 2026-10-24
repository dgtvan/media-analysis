"""Stage 'read': Claude looks at the candidate frames in batches, drops irrelevant ones and reads their text."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.providers.llm import Image, Text
from media_analysis.understand.models import Ingest
from media_analysis.workflows.report import prompts
from media_analysis.workflows.report.models import Candidates, Evidence, EvidenceItem, FramePlan
from media_analysis.workflows.report.templates.registry import Template


class ReadStage(Stage):
    name = "read"

    def run(self, ingest: Ingest, plan: FramePlan, candidates: Candidates, template: Template) -> Evidence:
        rep = self.rep
        if not candidates.candidates:
            rep.skipped("no frames")
            return Evidence(items=[])
        key = self.key(candidates, plan, template.id, self.settings.llm.model, self.settings.llm.effort)
        cached = None if self.force else self.cache.load(self.name, key, Evidence)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started("asking Claude to check frames and read their text")
        batch_size = self.settings.evidence.vision_batch_size
        items: list[EvidenceItem] = []
        cands = candidates.candidates
        for b in range(0, len(cands), batch_size):
            rep.progress(b, len(cands))
            batch = cands[b: b + batch_size]
            content: list = [Text(f"Report type: {template.name}. Examine these {len(batch)} frames.")]
            for c in batch:
                req = plan.requests[c.request_index]
                f = ingest.by_id(c.file_id)
                content.append(Text(
                    f"Frame {c.id} — {f.id} ({f.name}) at {format_timecode(c.time)}. "
                    f"Requested because: {req.reason} Look for: {req.look_for}"
                ))
                content.append(Image(Path(c.path)))
            data = self.ctx.llm.generate_json(prompts.READ_SYSTEM, content, prompts.READ_SCHEMA, rep.activity)
            by_id = {c.id: c for c in batch}
            for fr in data.get("frames", []):
                c = by_id.get(fr.get("id"))
                if c is None or not fr.get("relevant"):
                    continue
                items.append(EvidenceItem(
                    id=f"E{len(items) + 1:02d}", file_id=c.file_id, time=c.time, path=c.path,
                    caption=fr.get("caption", "").strip(), description=fr.get("description", "").strip(),
                    text=fr.get("text", "").strip(),
                ))
        result = Evidence(items=items)
        self.cache.save(self.name, key, result)
        rep.done(f"{len(items)} of {len(cands)} frame(s) kept")
        return result
