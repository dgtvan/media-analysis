"""Stage 'correct': Claude reads the whole transcript and fixes words speech recognition clearly misheard, mostly
English technical terms and names inside Vietnamese speech. Lines keep their language, times and wording otherwise;
only changed lines come back, and each change is kept so it can be checked."""

from __future__ import annotations

from pydantic import BaseModel

from media_analysis.engine.settings import read_terms
from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.providers.llm import Text
from media_analysis.understand.languages import describe, language_shares
from media_analysis.understand.models import Ingest, Transcript

CORRECT_SYSTEM = """\
You correct automatic speech recognition errors in a meeting transcript. Each line is "[id] time speaker: text".

Fix only words that were clearly misheard, using the context of the whole meeting and the known terms: English
technical terms, product and tool names, acronyms, people's names, ticket numbers and other numbers, often spoken
inside Vietnamese sentences and written as Vietnamese-sounding words (for example "subcription" for "subscription",
"layon" for "client", "airport" for "endpoint").

Keep each line's language, wording, word order and fillers ("ờ", "à", "um"). Do not translate, summarise, rephrase,
add punctuation for style, merge or split lines, or add anything that wasn't said. When you are not sure what was
meant, leave the line as it is.

Return only the lines you changed, each with its id and its full corrected text."""

CORRECT_SCHEMA = {
    "type": "object",
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["corrections"],
    "additionalProperties": False,
}

CORRECTIONS_FILE = "transcript-corrections.md"
PART_CHARS = 30_000
"""Lines per Claude call, by length; each call also gets the known terms but not the other parts."""


class Correction(BaseModel):
    file_id: str
    start: float
    before: str
    after: str


class CorrectedTranscript(BaseModel):
    transcript: Transcript
    corrections: list[Correction]


class CorrectStage(Stage):
    name = "correct"

    def run(self, ingest: Ingest, transcript: Transcript) -> CorrectedTranscript:
        rep = self.rep
        lines = [
            (ft.file_id, i, seg.start, ingest.by_id(ft.file_id).speaker, seg.text.strip())
            for ft in transcript.files for i, seg in enumerate(ft.segments) if seg.text.strip()
        ]
        if not lines:
            rep.skipped("no speech")
            return CorrectedTranscript(transcript=transcript, corrections=[])
        lines.sort(key=lambda line: line[2])
        terms = read_terms(self.settings.transcription.terms_file)
        llm = self.settings.llm
        key = self.key(transcript, terms, llm.model, llm.effort)
        cached = None if self.force else self.cache.load(self.name, key, CorrectedTranscript)
        if cached is not None:
            rep.skipped()
            return cached

        self.need_work()
        context = [f"Languages of the speech: {describe(language_shares(transcript)) or 'unknown'}."]
        context.append("Known terms:\n" + "\n".join(f"- {t}" for t in terms) if terms else "Known terms: none given.")
        parts: list[list[int]] = [[]]
        size = 0
        for n, line in enumerate(lines):
            if parts[-1] and size + len(line[4]) > PART_CHARS:
                parts.append([])
                size = 0
            parts[-1].append(n)
            size += len(line[4]) + 20
        rep.started(f"{len(lines)} lines in {len(parts)} part(s)")

        changed: dict[int, str] = {}
        for p, part in enumerate(parts):
            rep.progress(p, len(parts), f"part {p + 1} of {len(parts)}")
            text = "\n".join(
                f"[{n}] {format_timecode(lines[n][2])} {lines[n][3] or 'Speaker'}: {lines[n][4]}" for n in part)
            data = self.ctx.llm.generate_json(
                CORRECT_SYSTEM, [Text("\n\n".join(context)), Text("# Transcript\n" + text)], CORRECT_SCHEMA, rep.activity)
            allowed = set(part)
            for c in data.get("corrections") or []:
                n, new = c.get("id"), str(c.get("text", "")).strip()
                if n in allowed and new and new != lines[n][4]:
                    changed[n] = new

        files = {ft.file_id: ft.model_copy(deep=True) for ft in transcript.files}
        corrections: list[Correction] = []
        for n, new in sorted(changed.items()):
            file_id, i, start, _, before = lines[n]
            seg = files[file_id].segments[i]
            # Word times belong to the recognised words; they no longer match corrected text.
            files[file_id].segments[i] = seg.model_copy(update={"text": new, "words": None})
            corrections.append(Correction(file_id=file_id, start=start, before=before, after=new))
        result = CorrectedTranscript(
            transcript=Transcript(files=[files[ft.file_id] for ft in transcript.files]), corrections=corrections)
        self.cache.save(self.name, key, result)
        rep.done(f"{len(corrections)} of {len(lines)} lines corrected")
        return result


def corrections_markdown(ingest: Ingest, corrections: list[Correction]) -> str:
    """The corrections as a list to check: time, file and the line before and after."""
    lines = ["# Transcript corrections", "",
             "Words Claude corrected in the speech-recognition transcript before the report was written.", ""]
    for c in corrections:
        f = ingest.by_id(c.file_id)
        lines += [f"- **{format_timecode(c.start)}** {f.speaker or f.name}", f"  - before: {c.before}", f"  - after: {c.after}"]
    return "\n".join(lines) + "\n"
