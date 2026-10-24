"""Stage 'assemble': writes the deliverable, report.md plus only the frames and extracted text the report references."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.understand.models import Ingest
from media_analysis.workflows.report.models import Classification, Evidence, EvidenceItem, ReportDraft
from media_analysis.workflows.report.templates.registry import Template, render_template

_EVIDENCE_IMAGE = re.compile(r"!\[([^\]]*)\]\(evidence:([A-Za-z0-9_-]+)\)")
_EVIDENCE_ANY = re.compile(r"\(evidence:([A-Za-z0-9_-]+)\)")


class AssembleStage(Stage):
    name = "assemble"

    def run(
        self, output_dir: Path, draft: ReportDraft, evidence: Evidence, ingest: Ingest, template: Template,
        classification: Classification,
    ) -> tuple[Path, list[str]]:
        self.rep.started("writing output folder")
        report_path, warnings = assemble_output(output_dir, draft, evidence, ingest, template, classification)
        for w in warnings:
            self.rep.warning(w)
        self.rep.done(str(report_path))
        return report_path, warnings


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")[:60] or "media"


def assemble_output(
    output_dir: Path,
    draft: ReportDraft,
    evidence: Evidence,
    ingest: Ingest,
    template: Template,
    classification: Classification,
) -> tuple[Path, list[str]]:
    warnings: list[str] = []
    reference = output_dir / "reference"
    # reference/ is owned by the app; rebuilding it drops frames a previous run referenced but this one doesn't.
    shutil.rmtree(reference, ignore_errors=True)
    frames_dir, text_dir = reference / "frames", reference / "text"
    by_id = {e.id: e for e in evidence.items}
    written: dict[str, tuple[str, str | None]] = {}

    def materialise(item: EvidenceItem) -> tuple[str, str | None]:
        if item.id in written:
            return written[item.id]
        f = ingest.by_id(item.file_id)
        base = f"{_safe_stem(f.name)}_{format_timecode(item.time, '-')}"
        name, n = base, 2
        while (frames_dir / f"{name}.jpg").exists():
            name, n = f"{base}_{n}", n + 1
        frames_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item.path, frames_dir / f"{name}.jpg")
        text_rel = None
        if item.text:
            text_dir.mkdir(parents=True, exist_ok=True)
            (text_dir / f"{name}.md").write_text(
                f"# Text in frame — {f.name} at {format_timecode(item.time)}\n\n```text\n{item.text}\n```\n",
                encoding="utf-8",
            )
            text_rel = f"reference/text/{name}.md"
        written[item.id] = (f"reference/frames/{name}.jpg", text_rel)
        return written[item.id]

    def replace_image(m: re.Match) -> str:
        caption, eid = m.group(1).strip(), m.group(2)
        item = by_id.get(eid)
        if item is None:
            warnings.append(f"report referenced unknown evidence id {eid}; removed")
            return ""
        img, text_rel = materialise(item)
        f = ingest.by_id(item.file_id)
        line = f"*{caption or item.caption} — {f.name} at {format_timecode(item.time)}*"
        if text_rel:
            line += f" · [text in frame]({text_rel})"
        return f"![{caption or item.caption}]({img})\n\n{line}"

    def replace_link(m: re.Match) -> str:
        item = by_id.get(m.group(1))
        if item is None:
            warnings.append(f"report linked unknown evidence id {m.group(1)}; link removed")
            return "()"
        return f"({materialise(item)[0]})"

    body = _EVIDENCE_IMAGE.sub(replace_image, draft.markdown)
    body = _EVIDENCE_ANY.sub(replace_link, body)

    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        title, rest = lines[0], lines[1:]
    else:
        title, rest = f"# {draft.title}", lines
    while rest and not rest[0].strip():
        rest = rest[1:]
    if classification.fallback:
        rest += ["", *_template_fit(classification, reference)]
    sources = ", ".join(f"{f.name} ({format_timecode(f.duration)})" for f in ingest.files)
    chosen = f"{template.name} (`{template.id}`)"
    if classification.forced:
        chosen += " — selected by user"
    elif classification.fallback:
        chosen += " — no template fit, see [Template fit](#template-fit)"
    meta = [
        "",
        f"> **Processing type:** {chosen}  ",
        f"> **Sources:** {sources}  ",
        f"> **Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    report_path = output_dir / "report.md"
    report_path.write_text("\n".join([title, *meta, *rest]).rstrip() + "\n", encoding="utf-8")
    unused = len(evidence.items) - len(written)
    if unused > 0:
        warnings.append(f"{unused} evidence frame(s) were not used by the report and were left out")
    return report_path, warnings


def _template_fit(c: Classification, reference: Path) -> list[str]:
    """The section explaining why no template fit, and a ready-to-edit template file for this kind of recording."""
    lines = [
        "## Template fit",
        "",
        "No existing template fits this recording, so this is a general report.",
        "",
        f"**Why:** {c.reason}",
    ]
    if c.closest:
        lines += ["", "**Closest templates:** " + ", ".join(f"`{t}`" for t in c.closest)]
    if c.mismatch:
        lines += ["", "**Why they don't fit:**", *[f"- {m}" for m in c.mismatch]]
    if c.suggested:
        s = c.suggested
        reference.mkdir(parents=True, exist_ok=True)
        (reference / "suggested-template.md").write_text(
            render_template(s.id, s.name, s.description, s.signals, s.evidence, s.sections), encoding="utf-8"
        )
        lines += [
            "",
            f"**Suggested new template:** `{s.id}` ({s.name}): {s.description}",
            "",
            "A ready-to-edit copy is in [reference/suggested-template.md](reference/suggested-template.md). Put it in your "
            "templates folder to use it for recordings like this one, or widen the signals of one of the closest templates.",
        ]
    return lines
