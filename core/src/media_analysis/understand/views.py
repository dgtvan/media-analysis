"""Compact text views of what the understanding stages found, the way Claude reads them.

Speech is shown as phrases: consecutive segments of one speaker are joined while the gaps between them stay short and
the phrase stays under a length limit, so the text carries fewer timestamps and labels, and every line says when it
starts and ends. Long stretches without speech are marked, since they often mean a screen share, a demo or a break.
Times are on the shared clock of files recorded together (MediaFile.clock), so a video that started a little after
its audio lines up with what was said.
"""

from __future__ import annotations

from dataclasses import dataclass

from media_analysis.engine.timecode import format_timecode
from media_analysis.understand.languages import language_shares, transcript_note
from media_analysis.understand.models import Ingest, Transcript, VisualScan
from media_analysis.understand.sessions import one_sided


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
            "These files were recorded at the same time, together with any video below, so all times are on one "
            "shared timeline. \"Me\" is the recording computer's microphone: the person who made the recording, and "
            "anyone else in the same room (several people when the meeting was held in a room). \"Others\" is "
            "everyone heard through the computer: the remote participants."
        )
        if note := one_sided(ingest.files):
            lines.append(note)
        parts = []
        for ft in together:
            mf = ingest.by_id(ft.file_id)
            parts += [
                (mf.clock(seg.start), mf.clock(seg.end), mf.speaker, seg.text.strip())
                for seg in ft.segments if seg.text.strip() and not is_noise(seg.text)
            ]
        lines += _lines(_phrases(parts, rules), rules) if parts else ["(no speech)"]
        lines.append("")
    for ft in transcript.files:
        mf = ingest.by_id(ft.file_id)
        if mf.speaker:
            continue
        starts = f", starts at {format_timecode(mf.offset)} on the shared timeline" if mf.offset >= 0.5 else ""
        lines.append(f"## {mf.id}: {mf.name} (duration {format_timecode(mf.duration)}{starts})")
        parts = [
            (mf.clock(seg.start), mf.clock(seg.end), None, seg.text.strip())
            for seg in ft.segments if seg.text.strip() and not is_noise(seg.text)
        ]
        if not mf.has_audio:
            lines.append("(no audio)")
        elif mf.silent:
            lines.append("(silent: the recorder measured no sound in it)")
        elif mf.duplicate_audio:
            lines.append("(its sound is the conversation above, so it isn't repeated here)")
        elif not parts:
            lines.append("(no speech)")
        else:
            lines += _lines(_phrases(parts, rules), rules)
        lines.append("")
    return "\n".join(lines)


def scan_view(scan: VisualScan, ingest: Ingest) -> str:
    """The visual segments as a table, on the shared clock."""
    if not scan.segments:
        return "(no video)"
    lines = ["file | # | start-end | kind | edge density"]
    for s in scan.segments:
        f = ingest.by_id(s.file_id)
        lines.append(
            f"{s.file_id} | {s.index} | {format_timecode(f.clock(s.start))}-{format_timecode(f.clock(s.end))} | {s.kind} "
            f"| {s.edge_density:.3f}"
        )
    return "\n".join(lines)

