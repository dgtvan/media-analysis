"""Compact text views of what the understanding stages found, the way Claude reads them.

Speech is shown as phrases: consecutive segments of one speaker are joined while the gaps between them stay short and
the phrase stays under a length limit, so the text carries fewer timestamps and labels, and every line says when it
starts and ends. Long stretches without speech are marked, since they often mean a screen share, a demo or a break.
"""

from __future__ import annotations

from dataclasses import dataclass

from media_analysis.engine.timecode import format_timecode
from media_analysis.understand.languages import language_shares, transcript_note
from media_analysis.understand.models import Descriptions, Ingest, ShotList, Transcript, VisualScan, Word


# Phrases Whisper is known to invent over silence or music (from video outros in its training data). A line that is
# only this is noise, not speech.
NOISE_PATTERNS = (
    "subscribe cho kênh", "đăng ký kênh", "đăng kí kênh", "thanks for watching", "thank you for watching",
    "like and subscribe", "không bỏ lỡ những video",
)


def is_noise(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in NOISE_PATTERNS)


@dataclass(frozen=True)
class PhraseRules:
    merge_gap: float = 1.0
    """Segments of one speaker closer than this (seconds) join into one phrase."""
    max_phrase: float = 15.0
    """A phrase stops growing at this length (seconds); short enough that a line's start time points at what it says."""
    pause_mark: float = 20.0
    """Stretches without speech at least this long get their own line."""


@dataclass
class _Phrase:
    start: float
    end: float
    speaker: str | None
    text: str


def _phrases(parts: list[tuple[float, float, str | None, str]], rules: PhraseRules) -> list[_Phrase]:
    out: list[_Phrase] = []
    for start, end, speaker, text in sorted(parts, key=lambda p: p[0]):
        last = out[-1] if out else None
        if (last and last.speaker == speaker and start - last.end <= rules.merge_gap
                and end - last.start <= rules.max_phrase):
            last.end = max(last.end, end)
            last.text = f"{last.text} {text}"
        else:
            out.append(_Phrase(start, end, speaker, text))
    return out


def _lines(phrases: list[_Phrase], rules: PhraseRules) -> list[str]:
    lines: list[str] = []
    prev_end: float | None = None
    for p in phrases:
        if prev_end is not None and p.start - prev_end >= rules.pause_mark:
            lines.append(f"(no speech for {_duration(p.start - prev_end)})")
        who = f"{p.speaker}: " if p.speaker else ""
        lines.append(f"[{format_timecode(p.start)}-{format_timecode(p.end)}] {who}{p.text}")
        prev_end = p.end if prev_end is None else max(prev_end, p.end)
    return lines


def _duration(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def transcript_view(ingest: Ingest, transcript: Transcript, rules: PhraseRules = PhraseRules()) -> str:
    """Timecoded phrases per file. Files with a speaker were recorded together, so their speech is merged into one
    conversation on a shared timeline, each line labelled with its speaker. Speech that isn't all English starts
    with a note on its languages."""
    lines: list[str] = []
    if note := transcript_note(language_shares(transcript)):
        lines += [note, ""]
    together = [ft for ft in transcript.files if ingest.by_id(ft.file_id).speaker]
    if together:
        sources = ", ".join(f"{mf.id} ({mf.name}) = {mf.speaker}" for mf in (ingest.by_id(ft.file_id) for ft in together))
        lines.append(f"## Conversation: {sources}")
        lines.append(
            "These files were recorded at the same time, together with any silent video below, so all times are on "
            "one shared timeline. \"Me\" is the person who made the recording (microphone); \"Others\" is everyone "
            "heard through the computer."
        )
        parts = [
            (seg.start, seg.end, ingest.by_id(ft.file_id).speaker, seg.text.strip())
            for ft in together for seg in ft.segments if seg.text.strip() and not is_noise(seg.text)
        ]
        lines += _lines(_phrases(parts, rules), rules) if parts else ["(no speech)"]
        lines.append("")
    for ft in transcript.files:
        mf = ingest.by_id(ft.file_id)
        if mf.speaker:
            continue
        lines.append(f"## {mf.id}: {mf.name} (duration {format_timecode(mf.duration)})")
        parts = [(seg.start, seg.end, None, seg.text.strip()) for seg in ft.segments if seg.text.strip() and not is_noise(seg.text)]
        if not mf.has_audio:
            lines.append("(no audio)")
        elif mf.duplicate_audio:
            lines.append("(its sound is the conversation above, so it isn't repeated here)")
        elif not parts:
            lines.append("(no speech)")
        else:
            lines += _lines(_phrases(parts, rules), rules)
        lines.append("")
    return "\n".join(lines)


def scan_view(scan: VisualScan) -> str:
    """The visual segments as a table."""
    if not scan.segments:
        return "(no video)"
    lines = ["file | # | start-end | kind | edge density"]
    for s in scan.segments:
        lines.append(
            f"{s.file_id} | {s.index} | {format_timecode(s.start)}-{format_timecode(s.end)} | {s.kind} | {s.edge_density:.3f}"
        )
    return "\n".join(lines)


def shot_view(ingest: Ingest, shots: ShotList, descriptions: Descriptions | None = None) -> str:
    """One line per shot: id, times, what it shows and its quality, grouped by clip."""
    described = {(d.file_id, d.index): d for d in (descriptions.shots if descriptions else [])}
    lines: list[str] = []
    current = None
    for s in shots.shots:
        if s.file_id != current:
            current = s.file_id
            mf = ingest.by_id(s.file_id)
            lines.append(f"## {mf.id}: {mf.name} (duration {format_timecode(mf.duration)})")
        d = described.get((s.file_id, s.index))
        what = d.caption if d else "(not described)"
        extra = []
        if d and d.tags:
            extra.append("tags: " + ", ".join(d.tags))
        if d and d.issues:
            extra.append("issues: " + ", ".join(d.issues))
        quality = f"sharp {s.sharpness:.0f}, shake {s.shake:.1f}, light {s.brightness:.2f}"
        lines.append(
            f"{s.file_id}#{s.index} [{format_timecode(s.start)}-{format_timecode(s.end)}] {what}"
            + (f" ({'; '.join(extra)})" if extra else "") + f" [{quality}]"
        )
    return "\n".join(lines) if lines else "(no video)"


@dataclass
class Phrase:
    """A phrase of one file with an id Claude can refer to (f1.p12) and the words it is made of."""

    id: str
    file_id: str
    start: float
    end: float
    text: str
    words: list[Word]


def file_phrases(ingest: Ingest, transcript: Transcript, rules: PhraseRules = PhraseRules()) -> dict[str, list[Phrase]]:
    """Phrases per file whose sound is used, numbered within the file."""
    out: dict[str, list[Phrase]] = {}
    for ft in transcript.files:
        if not ingest.by_id(ft.file_id).uses_audio:
            continue
        phrases: list[Phrase] = []
        for seg in sorted(ft.segments, key=lambda s: s.start):
            text = seg.text.strip()
            if not text or is_noise(text):
                continue
            words = seg.words or [Word(start=seg.start, end=seg.end, text=text)]
            last = phrases[-1] if phrases else None
            if last and seg.start - last.end <= rules.merge_gap and seg.end - last.start <= rules.max_phrase:
                last.end = max(last.end, seg.end)
                last.text = f"{last.text} {text}"
                last.words += words
            else:
                phrases.append(Phrase(f"{ft.file_id}.p{len(phrases) + 1}", ft.file_id, seg.start, seg.end, text, list(words)))
        out[ft.file_id] = phrases
    return out
