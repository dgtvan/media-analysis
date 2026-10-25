"""Changes to an edit plan, as small operations that check their arguments: the first suggestion is built with them
and every chat change goes through them. Each returns a one-line summary of what it did; a PickError explains why it
couldn't."""

from __future__ import annotations

from media_analysis.workflows.edit.cuts import (
    CutRules, Footage, PickError, anchored, find_item, layout, next_id, next_label, shot_cut, speech_cut,
)
from media_analysis.workflows.edit.models import Clip, EditFormat, EditPlan, Mark, Track
from media_analysis.workflows.edit.view import t


def empty_plan(profile_id: str, fmt: EditFormat) -> EditPlan:
    return EditPlan(profile_id=profile_id, format=fmt, tracks=[
        Track(id="T1", name="Captions", kind="text"),
        Track(id="V2", name="B-roll", kind="video"),
        Track(id="V1", name="Main", kind="video"),
        Track(id="A1", name="Dialogue", kind="audio", linked="V1"),
        Track(id="A2", name="Music", kind="music"),
        Track(id="A3", name="Sound effects", kind="sfx"),
    ])


class Draft:
    def __init__(self, plan: EditPlan, footage: Footage, rules: CutRules = CutRules()) -> None:
        self.plan = plan
        self.footage = footage
        self.rules = rules
        self.changes: list[str] = []

    # --- helpers ------------------------------------------------------------------------------------------------

    def _track(self, track_id: str, kind: str | None = None) -> Track:
        tr = self.plan.track(track_id)
        if tr is None:
            raise PickError(f"There is no track {track_id}.")
        if tr.linked:
            raise PickError(f"{track_id} follows {tr.linked}; change {tr.linked} instead.")
        if kind and tr.kind != kind and not (kind == "video" and tr.kind == "audio"):
            raise PickError(f"{track_id} is a {tr.kind} track.")
        return tr

    def _cut(self, phrase_from, phrase_to, start_at, end_after, shot, start, end) -> tuple[str, int, int, str]:
        if phrase_from and shot:
            raise PickError("Give either phrases or a shot, not both.")
        if phrase_from:
            return speech_cut(self.footage, phrase_from, phrase_to, start_at, end_after, self.rules)
        if shot:
            file_id, a, b = shot_cut(self.footage, shot, start, end, self.rules)
            return file_id, a, b, self._words_in(file_id, a, b)
        raise PickError("Say which footage to use: a phrase range (f1.p3 to f1.p5) or a shot (f2#4).")

    def _words_in(self, file_id: str, a: int, b: int) -> str:
        words = [w.text.strip() for w in self.footage.all_words(file_id) if a / 1000 <= w.start and w.end <= b / 1000]
        return " ".join(words)

    def _done(self, summary: str) -> str:
        layout(self.plan)
        self.changes.append(summary)
        limit = self.plan.format.max_seconds
        total = self.plan.duration() / 1000
        if limit and total > limit:
            # Told to Claude with every change, so it can make room before it finishes.
            return f"{summary} The edit is now {total:.1f} s, {total - limit:.1f} s over the {limit} s limit."
        return summary

    # --- footage clips ------------------------------------------------------------------------------------------

    def add_clip(
        self, *, track: str = "V1", phrase_from: str | None = None, phrase_to: str | None = None,
        start_at: str | None = None, end_after: str | None = None, shot: str | None = None,
        start: float | None = None, end: float | None = None, after: str | None = None, over: str | None = None,
        offset: float = 0.0, role: str = "", reason: str = "",
    ) -> str:
        tr = self._track(track, "video")
        file_id, a, b, said = self._cut(phrase_from, phrase_to, start_at, end_after, shot, start, end)
        clip = Clip(id=next_id(self.plan, "c"), label=next_label(self.plan, "#"), file_id=file_id, src_in=a, src_out=b,
                    at=0, role=role, reason=reason, transcript=said)
        if tr.id == "V1":
            if over is not None:
                raise PickError("Main-track clips play in sequence; use `after`, not `over`.")
            index = self._index_after(tr, after)
            tr.clips.insert(index, clip)
            where = f"after {after}" if after else ("at the start" if index == 0 else "at the end")
        else:
            if over is None:
                raise PickError(f"Say which main clip it goes over (`over`), e.g. {tr.id} clip over #3.")
            clip.anchor, clip.offset = anchored(self.plan, over, offset)
            clip.muted = True
            tr.clips.append(clip)
            where = f"over {over} at +{offset:.1f}s"
        return self._done(f"Added {clip.label} on {tr.id} ({file_id} {t(a / 1000)}-{t(b / 1000)}) {where}.")

    def _index_after(self, tr: Track, after: str | None) -> int:
        if after is None:
            return len(tr.clips)
        if after.lower() in ("start", "beginning"):
            return 0
        for i, c in enumerate(tr.clips):
            if c.label.lower() == after.lower():
                return i + 1
        raise PickError(f"{after} is not on {tr.id}.")

    def replace_clip(self, label: str, **pick) -> str:
        track_id, item = find_item(self.plan, label)
        if not isinstance(item, Clip):
            raise PickError(f"{label} is not a footage clip.")
        file_id, a, b, said = self._cut(
            pick.get("phrase_from"), pick.get("phrase_to"), pick.get("start_at"), pick.get("end_after"),
            pick.get("shot"), pick.get("start"), pick.get("end"),
        )
        item.file_id, item.src_in, item.src_out, item.transcript = file_id, a, b, said
        if pick.get("reason"):
            item.reason = pick["reason"]
        return self._done(f"Replaced {label} with {file_id} {t(a / 1000)}-{t(b / 1000)}.")

    def trim_clip(
        self, label: str, *, start_at: str | None = None, end_after: str | None = None,
        trim_start: float = 0.0, trim_end: float = 0.0,
    ) -> str:
        """Start at / end after given words, or move the edges by seconds (positive = shorter)."""
        _, item = find_item(self.plan, label)
        if not isinstance(item, Clip):
            raise PickError(f"{label} is not a footage clip.")
        a, b = item.src_in, item.src_out
        if start_at or end_after:
            words = [w for w in self.footage.all_words(item.file_id) if a / 1000 - 0.3 <= w.start and w.end <= b / 1000 + 0.3]
            phrases = [p for p in self.footage.phrases.get(item.file_id, []) if any(w in p.words for w in words)]
            if not phrases:
                raise PickError(f"{label} has no speech to cut at.")
            file_id, a2, b2, said = speech_cut(
                self.footage, phrases[0].id, phrases[-1].id, start_at, end_after, self.rules,
            )
            a = a2 if start_at else a
            b = b2 if end_after else b
            item.transcript = said if (start_at and end_after) else self._words_in(item.file_id, a, b)
        a += int(trim_start * 1000)
        b -= int(trim_end * 1000)
        if b - a < self.rules.min_clip:
            raise PickError(f"That would leave {label} {max(0, b - a) / 1000:.1f} s long.")
        if a < 0 or b > self.footage.duration_ms(item.file_id):
            raise PickError(f"{label} can't extend past its footage ({t(self.footage.duration_ms(item.file_id) / 1000)}).")
        before = item.dur
        item.src_in, item.src_out = a, b
        if not (start_at or end_after):
            item.transcript = self._words_in(item.file_id, a, b)
        return self._done(f"Trimmed {label}: {before / 1000:.1f} s -> {item.dur / 1000:.1f} s.")

    def move_clip(self, label: str, *, after: str | None = None, over: str | None = None, offset: float = 0.0) -> str:
        track_id, item = find_item(self.plan, label)
        tr = self._track(track_id)
        if track_id == "V1":
            if over is not None:
                raise PickError("Main-track clips play in sequence; use `after`.")
            tr.clips.remove(item)  # type: ignore[arg-type]
            tr.clips.insert(self._index_after(tr, after), item)  # type: ignore[arg-type]
            return self._done(f"Moved {label} {'after ' + after if after else 'to the end'}.")
        if over is None:
            raise PickError(f"Say which main clip {label} goes over (`over`).")
        item.anchor, item.offset = anchored(self.plan, over, offset)
        return self._done(f"Moved {label} over {over} at +{offset:.1f}s.")

    def remove(self, label: str) -> str:
        track_id, item = find_item(self.plan, label)
        tr = self._track(track_id)
        if isinstance(item, Clip):
            tr.clips.remove(item)
        else:
            tr.marks.remove(item)
        return self._done(f"Removed {label} from {track_id}.")

    # --- text, music, sound effects --------------------------------------------------------------------------------

    def add_mark(self, *, track: str, text: str, over: str | None = None, offset: float = 0.0, duration: float = 3.0) -> str:
        tr = self._track(track)
        if tr.kind not in ("text", "music", "sfx"):
            raise PickError(f"{track} holds footage; captions, music and sound effects go on text/music/sfx tracks.")
        prefix = {"text": "T", "music": "M", "sfx": "FX"}[tr.kind]
        anchor, off = anchored(self.plan, over, offset)
        mark = Mark(id=next_id(self.plan, prefix.lower()), label=next_label(self.plan, prefix), at=off, dur=int(duration * 1000),
                    text=text, anchor=anchor, offset=off)
        tr.marks.append(mark)
        return self._done(f'Added {mark.label} on {tr.id}: "{text}".')

    def set_text(self, label: str, text: str, duration: float | None = None) -> str:
        _, item = find_item(self.plan, label)
        if not isinstance(item, Mark):
            raise PickError(f"{label} is footage; only captions, music and sound effects have text.")
        item.text = text
        if duration is not None:
            item.dur = int(duration * 1000)
        return self._done(f'{label} now reads "{text}".')

    def set_music(self, text: str) -> str:
        tr = self._track("A2")
        total = sum(c.dur for c in self._track("V1").clips)
        if tr.marks:
            tr.marks[0].text, tr.marks[0].dur = text, total
            return self._done(f'Music is now "{text}".')
        tr.marks.append(Mark(id=next_id(self.plan, "m"), label=next_label(self.plan, "M"), at=0, dur=total, text=text))
        return self._done(f'Added music "{text}".')

    # --- tracks ----------------------------------------------------------------------------------------------------

    def merge_tracks(self, source: str, into: str) -> str:
        src, dst = self._track(source), self._track(into)
        if source == "V1":
            raise PickError("The main track can't be merged away; merge other tracks into it instead.")
        if src.kind != dst.kind and not {src.kind, dst.kind} <= {"music", "sfx"}:
            raise PickError(f"{source} ({src.kind}) and {into} ({dst.kind}) hold different kinds of items.")
        if into == "V1":
            for c in sorted(src.clips, key=lambda c: c.at):
                c.anchor, c.offset, c.muted = None, 0, False
                dst.clips.insert(self._index_at(dst, c.at), c)
        else:
            dst.clips += src.clips
        dst.marks += src.marks
        self.plan.tracks.remove(src)
        return self._done(f"Merged {source} into {into}.")

    def _index_at(self, tr: Track, at: int) -> int:
        """Where a clip that started at `at` goes in a sequence: after the clip it played over."""
        for i, c in enumerate(tr.clips):
            if c.at > at:
                return i
        return len(tr.clips)

    def split_track(self, track: str, labels: list[str], name: str) -> str:
        src = self._track(track)
        if track == "V1":
            raise PickError("Main-track clips play in sequence; move b-roll to its own track with labels from V2.")
        ids = {lbl.lower() for lbl in labels}
        moving_clips = [c for c in src.clips if c.label.lower() in ids]
        moving_marks = [m for m in src.marks if m.label.lower() in ids]
        if len(moving_clips) + len(moving_marks) != len(ids):
            raise PickError(f"Not all of {', '.join(labels)} are on {track}.")
        prefix = {"video": "V", "audio": "A", "text": "T", "music": "A", "sfx": "A"}[src.kind]
        used = {tr.id for tr in self.plan.tracks}
        n = 1
        while f"{prefix}{n}" in used:
            n += 1
        new = Track(id=f"{prefix}{n}", name=name, kind=src.kind, clips=moving_clips, marks=moving_marks)
        src.clips = [c for c in src.clips if c not in moving_clips]
        src.marks = [m for m in src.marks if m not in moving_marks]
        self.plan.tracks.insert(self.plan.tracks.index(src), new)
        return self._done(f"Moved {', '.join(labels)} from {track} to a new track {new.id} ({name}).")

    def rename_track(self, track: str, name: str) -> str:
        tr = self.plan.track(track)
        if tr is None:
            raise PickError(f"There is no track {track}.")
        tr.name = name
        return self._done(f"{track} is now called {name}.")

    def set_strategy(self, text: str) -> str:
        self.plan.strategy = text
        return self._done("Updated the plan description.")
