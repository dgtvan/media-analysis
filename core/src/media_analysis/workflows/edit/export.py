"""The edit for people and other programs: `edit.json` (seconds, original file paths, everything needed to rebuild the
timeline in any editor) and `edit.md` (a readable shot list)."""

from __future__ import annotations

import json
from pathlib import Path

from media_analysis.understand.models import Ingest
from media_analysis.workflows.edit.models import EditPlan
from media_analysis.workflows.edit.view import t

EXPORT_SCHEMA = 1


def edit_json(plan: EditPlan, ingest: Ingest, version: int) -> dict:
    files = {f.id: f for f in ingest.files}
    used = {c.file_id for tr in plan.tracks for c in tr.clips}
    return {
        "schema": EXPORT_SCHEMA,
        "version": version,
        "profile": plan.profile_id,
        "format": plan.format.model_dump(),
        "duration": round(plan.duration() / 1000, 3),
        "strategy": plan.strategy,
        "sources": [
            {"id": fid, "name": files[fid].name, "path": files[fid].path, "duration": round(files[fid].duration, 3),
             "width": files[fid].width, "height": files[fid].height}
            for fid in sorted(used) if fid in files
        ],
        "tracks": [
            {
                "id": tr.id, "name": tr.name, "kind": tr.kind, "linked": tr.linked,
                "items": (
                    [{"label": c.label, "source": c.file_id, "in": c.src_in / 1000, "out": c.src_out / 1000,
                      "at": c.at / 1000, "duration": c.dur / 1000, "muted": c.muted, "role": c.role, "reason": c.reason,
                      "transcript": c.transcript} for c in tr.clips]
                    + [{"label": m.label, "text": m.text, "at": m.at / 1000, "duration": m.dur / 1000,
                        "placeholder": tr.kind in ("music", "sfx")} for m in tr.marks]
                ),
            }
            for tr in plan.tracks
        ],
    }


def edit_markdown(plan: EditPlan, ingest: Ingest, version: int) -> str:
    names = {f.id: f.name for f in ingest.files}
    lines = [
        f"# Edit (version {version})", "",
        f"{plan.duration() / 1000:.1f} s · {plan.format.aspect} · profile `{plan.profile_id}`", "",
    ]
    if plan.strategy:
        lines += [plan.strategy, ""]
    for tr in plan.tracks:
        if tr.linked:
            continue
        items = sorted([*tr.clips, *tr.marks], key=lambda i: i.at)
        if not items:
            continue
        lines += [f"## {tr.id} {tr.name}", "", "| | At | Length | Source | Content |", "|---|---|---|---|---|"]
        for c in tr.clips:
            content = (f'"{c.transcript}"' if c.transcript else c.reason).replace("|", "\\|")
            lines.append(f"| {c.label} | {t(c.at / 1000)} | {c.dur / 1000:.1f} s | {names.get(c.file_id, c.file_id)} "
                         f"{t(c.src_in / 1000)}-{t(c.src_out / 1000)} | {content} |")
        for m in tr.marks:
            lines.append(f"| {m.label} | {t(m.at / 1000)} | {m.dur / 1000:.1f} s | | {m.text.replace('|', chr(92) + '|')} |")
        lines.append("")
    return "\n".join(lines)


def write_exports(plan: EditPlan, ingest: Ingest, version: int, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "edit.json"
    j.write_text(json.dumps(edit_json(plan, ingest, version), ensure_ascii=False, indent=2), encoding="utf-8")
    m = out_dir / "edit.md"
    m.write_text(edit_markdown(plan, ingest, version), encoding="utf-8")
    return j, m
