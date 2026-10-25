"""Stage 'describe': Claude describes every shot from contact sheets of a few frames per shot, so later steps know
what each piece of footage shows without looking at it again. Results are cached per clip, so adding a clip only
describes that clip."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.cache import digest
from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.media import ffmpeg
from media_analysis.media.frames import contact_sheet
from media_analysis.providers.llm import Image, Text
from media_analysis.understand.models import Descriptions, Ingest, MediaFile, Shot, ShotDescription, ShotList

DESCRIBE_SYSTEM = """\
You describe shots of video footage so that an editor can find and choose them later without watching them.

You get one contact sheet: for each shot, three frames (start, middle, end) in a row, labelled with the shot's id and
time, plus local measurements per shot (sharpness, shake, brightness). For every shot return:
- caption: one English sentence saying what the shot shows (subject, action, setting, camera movement if notable).
- subjects: the main people, animals or objects, as short phrases.
- place: where it seems to be, briefly ("forest trail", "kitchen", "office meeting room"), or "".
- action: what happens, briefly, or "".
- tags: 3-8 short lowercase tags useful for searching (e.g. "b-roll", "talking to camera", "drone", "sunset", "text on
  screen").
- issues: visible problems that matter for editing ("blurry", "too dark", "shaky", "lens flare", "obstructed"), or [].
Describe only what is visible; do not guess names of people."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "caption": {"type": "string"},
                    "subjects": {"type": "array", "items": {"type": "string"}},
                    "place": {"type": "string"},
                    "action": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "issues": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "caption", "subjects", "place", "action", "tags", "issues"],
            },
        }
    },
    "required": ["shots"],
}

SHOTS_PER_SHEET = 8


def shot_id(s: Shot) -> str:
    return f"{s.file_id}#{s.index}"


class DescribeStage(Stage):
    name = "describe"

    def run(self, ingest: Ingest, shots: ShotList) -> Descriptions:
        rep = self.rep
        by_file: dict[str, list[Shot]] = {}
        for s in shots.shots:
            by_file.setdefault(s.file_id, []).append(s)
        if not by_file:
            rep.skipped("no video")
            return Descriptions(shots=[])
        llm = self.settings.llm
        out: list[ShotDescription] = []
        started = False
        for n, (fid, file_shots) in enumerate(by_file.items()):
            f = ingest.by_id(fid)
            plain = [s.model_copy(update={"file_id": ""}) for s in file_shots]
            key = self.key(f.fingerprint, plain, llm.model, llm.effort)
            cached = None if self.force else self.cache.load(self.name, key, Descriptions)
            if cached is None:
                if not started:
                    rep.started("asking Claude what each shot shows")
                    started = True
                rep.progress(n, len(by_file), f.name)
                self.need_work()
                cached = Descriptions(shots=self._describe_file(f, file_shots))
                self.cache.save(self.name, key, cached)
            out.extend(d.model_copy(update={"file_id": fid}) for d in cached.shots)
        if started:
            rep.done(f"{len(out)} shot(s) described")
        else:
            rep.skipped()
        return Descriptions(shots=out)

    def _describe_file(self, f: MediaFile, shots: list[Shot]) -> list[ShotDescription]:
        thumbs = self.cache.dir(self.name, "thumbs")
        result: list[ShotDescription] = []
        for b in range(0, len(shots), SHOTS_PER_SHEET):
            batch = shots[b: b + SHOTS_PER_SHEET]
            items: list[tuple[str, Path]] = []
            notes: list[str] = []
            for s in batch:
                for label, t in (("start", s.start + 0.2), ("mid", (s.start + s.end) / 2), ("end", max(s.start, s.end - 0.3))):
                    out = thumbs / f"{f.fingerprint}-{int(t * 1000)}.jpg"
                    if not out.exists():
                        ffmpeg.extract_still(Path(f.path), t, out, max_side=480)
                    items.append((f"{shot_id(s)} {label} {format_timecode(t)}", out))
                notes.append(
                    f"- {shot_id(s)}: {format_timecode(s.start)}-{format_timecode(s.end)}, sharpness {s.sharpness:.0f}, "
                    f"shake {s.shake:.1f}px, brightness {s.brightness:.2f}"
                )
            sheet = contact_sheet(items, thumbs / f"sheet-{digest([str(p) for _, p in items])}.jpg", columns=3)
            data = self.ctx.llm.generate_json(
                DESCRIBE_SYSTEM,
                [Text(f"Clip {f.id}: {f.name}\nShots:\n" + "\n".join(notes)), Image(sheet)],
                DESCRIBE_SCHEMA, self.rep.activity,
            )
            known = {shot_id(s): s for s in batch}
            for d in data.get("shots", []):
                s = known.pop(str(d.get("id", "")), None)
                if s is None:
                    continue
                result.append(ShotDescription(
                    file_id=f.id, index=s.index, caption=str(d.get("caption", "")).strip(),
                    subjects=[str(x) for x in d.get("subjects", [])], place=str(d.get("place", "")),
                    action=str(d.get("action", "")), tags=[str(x).lower() for x in d.get("tags", [])],
                    issues=[str(x) for x in d.get("issues", [])],
                ))
            # A shot Claude skipped still gets an entry, so later steps can tell it was looked at.
            result += [ShotDescription(file_id=f.id, index=s.index, caption="(not described)") for s in known.values()]
        return sorted(result, key=lambda d: d.index)
