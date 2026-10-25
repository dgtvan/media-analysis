"""Stage 'condense': when a transcript view is too long for the Claude calls that read it, Claude condenses it part by
part into timecoded notes. Shorter views pass through unchanged, without a Claude call."""

from __future__ import annotations

from pydantic import BaseModel

from media_analysis.engine.workflow import Stage
from media_analysis.providers.llm import Text

CONDENSE_SYSTEM = """\
You condense one part of a long, timecoded transcript so that a later step can work from your notes instead of the
full text. The transcript comes from automatic speech recognition (English, Vietnamese or mixed) and can contain
errors.

Keep everything a reader could need later: topics, decisions, action items with their owners, numbers, names, dates,
questions and answers, disagreements, and anything shown or demonstrated. Keep short verbatim quotes for important
statements, in the original language. Drop greetings, small talk, filler and repetition.

Write in English, one note per line, each starting with the time it was said: "- [hh:mm:ss] ...". Keep speaker labels
(such as "Me:" or "Others:") when the transcript has them. Output only the notes."""


class CondensedView(BaseModel):
    text: str
    condensed: bool
    parts: int = 1


def split_lines(text: str, max_chars: int) -> list[str]:
    """Splits text into parts of at most `max_chars`, only between lines (a single longer line becomes its own part)."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        if current and size + len(line) + 1 > max_chars:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


class CondenseStage(Stage):
    name = "condense"

    def run(self, view: str) -> CondensedView:
        rep = self.rep
        budget = self.settings.llm.transcript_budget_chars
        if len(view) <= budget:
            rep.skipped(f"fits in one call ({len(view):,} chars)")
            return CondensedView(text=view, condensed=False)
        llm = self.settings.llm
        key = self.key(view, budget, llm.model, llm.effort)
        cached = None if self.force else self.cache.load(self.name, key, CondensedView)
        if cached is not None:
            rep.skipped()
            return cached
        # Parts small enough that several condensed parts together stay well inside the budget.
        parts = split_lines(view, max(20_000, budget // 5))
        rep.started(f"{len(view):,} chars in {len(parts)} parts")
        notes: list[str] = []
        for i, part in enumerate(parts):
            rep.progress(i, len(parts), f"part {i + 1} of {len(parts)}")
            text = self.ctx.llm.generate_text(CONDENSE_SYSTEM, [Text(part)], rep.activity).strip()
            notes.append(f"## Notes on part {i + 1} of {len(parts)}\n{text}")
        result = CondensedView(
            text="(The transcript was too long to include in full; these are timecoded notes made from it part by part.)\n\n"
            + "\n\n".join(notes),
            condensed=True, parts=len(parts),
        )
        self.cache.save(self.name, key, result)
        rep.done(f"{len(parts)} parts, {len(view):,} → {len(result.text):,} chars")
        return result
