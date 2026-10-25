"""From Claude's choices to exact cut points, and from the main track's order to everyone's positions.

Claude never writes timecodes for cuts. It names a range of phrases (optionally starting or ending at given words)
or a shot (optionally a part of it); code turns that into milliseconds following these rules:
- cuts fall between words, never inside one; each edge gets a little air (80 ms before, 120 ms after), but never so
  much that it reaches into the neighbouring word;
- shots are cut within their own bounds;
- the main track plays its clips back to back; everything laid over it moves with the clip it belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from media_analysis.understand.models import Ingest, ShotList, Word
from media_analysis.understand.views import Phrase
from media_analysis.workflows.edit.models import Clip, EditPlan, Mark


class PickError(ValueError):
    """A choice that can't be turned into a cut; the message says why (it goes back to Claude)."""


@dataclass(frozen=True)
class CutRules:
    pad_before: int = 80
    pad_after: int = 120
    min_clip: int = 500


@dataclass
class Footage:
    """What the cut code needs to know about the footage."""

    ingest: Ingest
    phrases: dict[str, list[Phrase]]
    shots: ShotList

    def phrase(self, phrase_id: str) -> Phrase:
        file_id = phrase_id.split(".")[0]
        for p in self.phrases.get(file_id, []):
            if p.id == phrase_id:
                return p
        raise PickError(f"There is no phrase {phrase_id}.")

    def shot(self, shot_id: str):
        for s in self.shots.shots:
            if f"{s.file_id}#{s.index}" == shot_id:
                return s
        raise PickError(f"There is no shot {shot_id}.")

    def duration_ms(self, file_id: str) -> int:
        try:
            return int(self.ingest.by_id(file_id).duration * 1000)
        except StopIteration:
            raise PickError(f"There is no footage {file_id}.") from None

    def all_words(self, file_id: str) -> list[Word]:
        return [w for p in self.phrases.get(file_id, []) for w in p.words]


def _norm(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w']+", text.lower()) if t]


def _find(words: list[Word], quote: str, last: bool) -> tuple[int, int]:
    """Index range of the words matching `quote` (first or last occurrence)."""
    target = _norm(quote)
    if not target:
        raise PickError("An empty quote can't mark a cut.")
    tokens: list[tuple[str, int]] = [(t, i) for i, w in enumerate(words) for t in _norm(w.text)]
    hits = [k for k in range(len(tokens) - len(target) + 1) if [t for t, _ in tokens[k: k + len(target)]] == target]
    if not hits:
        raise PickError(f'The words "{quote}" are not in that range.')
    k = hits[-1] if last else hits[0]
    return tokens[k][1], tokens[k + len(target) - 1][1]


def speech_cut(
    footage: Footage, phrase_from: str, phrase_to: str | None = None, start_at: str | None = None,
    end_after: str | None = None, rules: CutRules = CutRules(),
) -> tuple[str, int, int, str]:
    """(file id, in ms, out ms, words said) for a range of phrases of one file."""
    first = footage.phrase(phrase_from)
    last = footage.phrase(phrase_to or phrase_from)
    if first.file_id != last.file_id:
        raise PickError("A cut can't span two files; make two clips.")
    phrases = footage.phrases[first.file_id]
    a, b = phrases.index(first), phrases.index(last)
    if b < a:
        raise PickError(f"{phrase_to} comes before {phrase_from}.")
    words = [w for p in phrases[a: b + 1] for w in p.words]
    i, j = 0, len(words) - 1
    if start_at:
        i = _find(words, start_at, last=False)[0]
    if end_after:
        j = _find(words[i:], end_after, last=True)[1] + i
    if j < i:
        raise PickError("The end comes before the start.")
    every = footage.all_words(first.file_id)
    pos_first = every.index(words[i])
    pos_last = every.index(words[j])
    prev_end = every[pos_first - 1].end if pos_first > 0 else 0.0
    next_start = every[pos_last + 1].start if pos_last + 1 < len(every) else None
    start_ms = int(words[i].start * 1000)
    end_ms = int(words[j].end * 1000)
    cut_in = max(start_ms - rules.pad_before, int(prev_end * 1000), 0)
    limit = int(next_start * 1000) if next_start is not None else footage.duration_ms(first.file_id)
    cut_out = min(end_ms + rules.pad_after, limit, footage.duration_ms(first.file_id))
    if cut_out - cut_in < rules.min_clip:
        cut_out = min(cut_in + rules.min_clip, footage.duration_ms(first.file_id))
    said = " ".join(w.text.strip() for w in words[i: j + 1])
    return first.file_id, cut_in, cut_out, said


def shot_cut(
    footage: Footage, shot_id: str, start: float | None = None, end: float | None = None, rules: CutRules = CutRules(),
) -> tuple[str, int, int]:
    """(file id, in ms, out ms) for a shot or a part of it (times in seconds of the clip, kept inside the shot)."""
    s = footage.shot(shot_id)
    a = s.start if start is None else min(max(start, s.start), s.end)
    b = s.end if end is None else min(max(end, s.start), s.end)
    if b - a < rules.min_clip / 1000:
        raise PickError(f"That part of {shot_id} is too short ({b - a:.1f} s); shots run {s.start:.1f}-{s.end:.1f} s.")
    return s.file_id, int(a * 1000), int(b * 1000)


def layout(plan: EditPlan) -> None:
    """Main-track clips back to back in list order; anchored items follow their clip; items whose clip is gone keep
    their place."""
    main = plan.track("V1")
    starts: dict[str, int] = {}
    if main:
        at = 0
        for c in main.clips:
            c.at, c.anchor, c.offset = at, None, 0
            starts[c.id] = at
            at += c.dur
    total = sum(c.dur for c in main.clips) if main else 0
    for t in plan.tracks:
        if t.id == "V1" or t.linked:
            continue
        if t.kind == "music" and len(t.marks) == 1 and t.marks[0].anchor is None and t.marks[0].at == 0:
            # One music bed for the whole edit follows its length.
            t.marks[0].dur = total
            continue
        for item in [*t.clips, *t.marks]:
            if item.anchor in starts:
                item.at = starts[item.anchor] + item.offset
            elif item.anchor is not None:
                item.anchor, item.offset = None, 0


def next_label(plan: EditPlan, prefix: str) -> str:
    """The next free label with this prefix: "#" for footage, "T" text, "M" music, "FX" sound effects."""
    numbers = [int(m.group(1)) for t in plan.tracks for item in [*t.clips, *t.marks]
               if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", item.label))]
    return f"{prefix}{max(numbers, default=0) + 1}"


def next_id(plan: EditPlan, prefix: str) -> str:
    numbers = [int(m.group(1)) for t in plan.tracks for item in [*t.clips, *t.marks]
               if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", item.id))]
    return f"{prefix}{max(numbers, default=0) + 1}"


def anchored(plan: EditPlan, over_label: str | None, offset_s: float) -> tuple[str | None, int]:
    """(anchor clip id, offset ms) for an item placed `offset_s` into the main clip labelled `over_label`."""
    if over_label is None:
        return None, int(offset_s * 1000)
    found = plan.by_label(over_label)
    if not found or found[0].id != "V1" or not isinstance(found[1], Clip):
        raise PickError(f"{over_label} is not a clip on the main track (V1).")
    clip = found[1]
    return clip.id, max(0, min(int(offset_s * 1000), clip.dur - 1))


def find_item(plan: EditPlan, label: str) -> tuple[str, Clip | Mark]:
    found = plan.by_label(label)
    if not found:
        raise PickError(f"There is no {label}.")
    return found[0].id, found[1]
