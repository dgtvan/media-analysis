"""What Claude reads to plan and change an edit: per clip its shots (what they show, how usable they are) and its
phrases with ids, and the current edit as a compact list."""

from __future__ import annotations

from media_analysis.understand.models import Descriptions, Ingest, ShotList, Sound
from media_analysis.understand.views import Phrase
from media_analysis.workflows.edit.models import EditPlan


def t(seconds: float) -> str:
    """mm:ss.s (h:mm:ss.s past an hour)."""
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}"


def footage_view(
    ingest: Ingest, phrases: dict[str, list[Phrase]], shots: ShotList, descriptions: Descriptions | None,
    sound: Sound | None = None,
) -> str:
    described = {(d.file_id, d.index): d for d in (descriptions.shots if descriptions else [])}
    loud = {s.file_id: s for s in (sound.files if sound else [])}
    out: list[str] = []
    for f in ingest.files:
        kind = "video" if f.has_video else "audio"
        extra = ""
        s = loud.get(f.id)
        if s and s.loudness is not None:
            extra = f", loudness {s.loudness:.0f} LUFS"
        out.append(f"## {f.id}: {f.name} ({kind}, {t(f.duration)}{extra})")
        file_shots = [x for x in shots.shots if x.file_id == f.id]
        if file_shots:
            out.append("Shots:")
            for x in file_shots:
                d = described.get((f.id, x.index))
                what = d.caption if d else "(not described)"
                notes = []
                if d and d.issues:
                    notes.append("issues: " + ", ".join(d.issues))
                if x.shake > 6:
                    notes.append("shaky")
                if x.brightness < 0.18:
                    notes.append("dark")
                note = f" [{'; '.join(notes)}]" if notes else ""
                out.append(f"- {f.id}#{x.index} {t(x.start)}-{t(x.end)} {what}{note}")
        file_phrases = phrases.get(f.id, [])
        if file_phrases:
            out.append("Speech:")
            out += [f"- {p.id} {t(p.start)}-{t(p.end)} {p.text}" for p in file_phrases]
        elif f.uses_audio:
            out.append("Speech: none")
        out.append("")
    return "\n".join(out)


def plan_view(plan: EditPlan, ingest: Ingest) -> str:
    """The current edit, track by track, with labels and times."""
    names = {f.id: f.name for f in ingest.files}
    lines = [f"Edit: {plan.duration() / 1000:.1f} s (target {plan.format.target_seconds} s, {plan.format.aspect})"]
    if plan.strategy:
        lines.append(f"Plan: {plan.strategy}")
    for tr in plan.tracks:
        if tr.linked:
            lines.append(f"{tr.id} {tr.name}: the sound of {tr.linked}")
            continue
        lines.append(f"{tr.id} {tr.name}:")
        for c in tr.clips:
            said = f' "{c.transcript}"' if c.transcript else ""
            over = " (muted)" if c.muted else ""
            lines.append(
                f"  {c.label} at {t(c.at / 1000)}-{t((c.at + c.dur) / 1000)} = {c.file_id} {names.get(c.file_id, '')} "
                f"{t(c.src_in / 1000)}-{t(c.src_out / 1000)}{over} [{c.role}]{said}"
            )
        for m in tr.marks:
            lines.append(f"  {m.label} at {t(m.at / 1000)}-{t((m.at + m.dur) / 1000)}: {m.text}")
        if not tr.clips and not tr.marks:
            lines.append("  (empty)")
    return "\n".join(lines)
