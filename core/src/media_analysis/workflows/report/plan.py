"""Stage 'plan': one Claude call decides which moments need a frame as evidence, guided by the template."""

from __future__ import annotations

from media_analysis.engine.workflow import Stage
from media_analysis.providers.llm import Text
from media_analysis.understand.models import Ingest, Transcript, VisualScan
from media_analysis.workflows.report import prompts
from media_analysis.workflows.report.models import EvidenceRequest, FramePlan
from media_analysis.workflows.report.templates.registry import Template


class PlanStage(Stage):
    name = "plan"

    def run(self, ingest: Ingest, transcript: Transcript, scan: VisualScan, template: Template) -> FramePlan:
        rep = self.rep
        videos = {f.id: f for f in ingest.files if f.has_video}
        if not videos:
            rep.skipped("no video")
            return FramePlan(requests=[])
        ev = self.settings.evidence
        key = self.key(
            transcript, scan, template.fingerprint_parts(), self.settings.llm.model, self.settings.llm.effort,
            ev.max_requests,
        )
        cached = None if self.force else self.cache.load(self.name, key, FramePlan)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started("asking Claude which moments need frames")
        template_text = (
            f"Template: {template.name}\nSections: {', '.join(template.sections)}\n"
            "Visual evidence that matters:\n" + "\n".join(f"- {e}" for e in template.evidence)
        )
        video_list = "\n".join(f"- {f.id}: {f.name}, duration {f.duration:.0f}s" for f in videos.values())
        content = [
            Text("# Report template\n" + template_text),
            Text("# Files with video\n" + video_list),
            Text("# Transcript\n" + transcript.to_prompt_text(ingest)),
            Text("# Visual segments\n" + scan.to_prompt_text()),
            Text(f"Return at most {ev.max_requests} requests."),
        ]
        data = self.ctx.llm.generate_json(prompts.PLAN_SYSTEM, content, prompts.plan_schema(ev.max_requests), rep.activity)
        requests: list[EvidenceRequest] = []
        for r in data.get("requests", []):
            f = videos.get(r.get("file_id"))
            if f is None:
                continue
            start = max(0.0, min(float(r["start"]), f.duration))
            end = max(start, min(float(r["end"]), f.duration))
            if end - start < 2:
                start, end = max(0.0, start - 1), min(f.duration, end + 1)
            end = min(end, start + 60)
            requests.append(EvidenceRequest(
                file_id=f.id, start=start, end=end, reason=r.get("reason", ""), look_for=r.get("look_for", ""),
                priority=int(r.get("priority", 2)),
            ))
        requests.sort(key=lambda r: (r.priority, r.file_id, r.start))
        plan = FramePlan(requests=requests[: ev.max_requests])
        self.cache.save(self.name, key, plan)
        rep.done(f"{len(plan.requests)} moment(s) requested")
        return plan
