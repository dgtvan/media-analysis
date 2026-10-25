"""Stage 'sequence': one Claude call plans the first edit from the footage view and the profile; code builds it with
the same operations the chat uses (draft.py), so every choice is checked and cut by the same rules."""

from __future__ import annotations

import json

from pydantic import BaseModel

from media_analysis.engine.workflow import Stage
from media_analysis.providers.llm import Text
from media_analysis.workflows.edit.cuts import Footage, PickError
from media_analysis.workflows.edit.draft import Draft, empty_plan
from media_analysis.workflows.edit.models import EditFormat, EditPlan
from media_analysis.workflows.edit.profiles.registry import EditProfile

SEQUENCE_SYSTEM = """\
You are a video editor. From the footage described below, plan a first edit that follows the profile.

The footage view lists, per clip, its shots (id like f2#3, times, what they show, problems) and its speech as phrases
(id like f1.p12, times, words). You never write cut times for speech: a main-track clip is a range of phrases
(`phrase_from`, optionally `phrase_to`), optionally starting at the first words you give in `start_at` and ending
after the words in `end_after` (copy them exactly from the phrase text). A clip without speech is a shot (`shot`),
optionally only part of it (`start`/`end` in seconds of that clip, inside the shot). Code cuts between words and adds
a little air at each edge.

Plan:
- `main`: the main track, in playing order. Keep sentences whole; drop retakes, false starts, fillers and dead air.
  Use the best take of anything said more than once. Respect the profile's clip lengths, pacing and structure, and
  aim for the target length.
- `broll`: cutaway shots laid over main clips (`over` = 1-based position in `main`, `offset` seconds into it), muted.
  Use them where they show what is being talked about, or to hide a jump cut. Skip if the profile has no b-roll.
- `captions`: short on-screen text over main clips, as the profile asks.
- `music`: one line describing the music to use, or "" for none. `sound_effects`: short descriptions placed over main
  clips (e.g. "whoosh" at a transition), only if the profile wants them.
- `strategy`: 2-4 sentences on the story you are telling and why.
Use only ids that exist in the view. Prefer sharp, steady, well-lit shots."""

_PICK = {
    "phrase_from": {"type": "string"}, "phrase_to": {"type": "string"}, "start_at": {"type": "string"},
    "end_after": {"type": "string"}, "shot": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
}

SEQUENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string"},
        "main": {"type": "array", "items": {"type": "object", "properties": {
            **_PICK, "role": {"type": "string"}, "reason": {"type": "string"}}, "required": ["role", "reason"]}},
        "broll": {"type": "array", "items": {"type": "object", "properties": {
            "over": {"type": "integer"}, "offset": {"type": "number"}, **_PICK, "reason": {"type": "string"}},
            "required": ["over", "offset", "shot", "reason"]}},
        "captions": {"type": "array", "items": {"type": "object", "properties": {
            "over": {"type": "integer"}, "offset": {"type": "number"}, "duration": {"type": "number"},
            "text": {"type": "string"}}, "required": ["over", "offset", "duration", "text"]}},
        "music": {"type": "string"},
        "sound_effects": {"type": "array", "items": {"type": "object", "properties": {
            "over": {"type": "integer"}, "offset": {"type": "number"}, "text": {"type": "string"}},
            "required": ["over", "offset", "text"]}},
    },
    "required": ["strategy", "main", "broll", "captions", "music", "sound_effects"],
}


class FirstEdit(BaseModel):
    plan: EditPlan
    warnings: list[str] = []


def profile_text(p: EditProfile, limit: int | None = None) -> str:
    cap = f" Hard limit: at most {limit} s in total; never longer." if limit else ""
    return (
        f"Profile: {p.name} - {p.description}\n"
        f"Cutting follows: {p.cutting}. Target length: {min(p.target_seconds, limit or p.target_seconds)} s.{cap} "
        f"Aspect: {p.aspect}. Pacing: {p.pacing}.\n"
        f"Clip length: {p.min_clip_seconds}-{p.max_clip_seconds} s. Structure: {', '.join(p.structure) or 'free'}.\n"
        f"Captions: {p.captions}. B-roll: {'yes' if p.broll else 'no'}. Music: {p.music or 'none'}. "
        f"Sound effects: {'yes' if p.sound_effects else 'no'}.\n\nGuidance:\n{p.instructions}"
    )


def build_first_edit(data: dict, profile: EditProfile, footage: Footage, limit: int | None = None) -> FirstEdit:
    target = min(profile.target_seconds, limit) if limit else profile.target_seconds
    plan = empty_plan(profile.id, EditFormat(aspect=profile.aspect, target_seconds=target, max_seconds=limit))
    plan.strategy = str(data.get("strategy", "")).strip()
    draft = Draft(plan, footage)
    warnings: list[str] = []
    labels: list[str | None] = []
    pick_keys = ("phrase_from", "phrase_to", "start_at", "end_after", "shot", "start", "end")

    def picks(item: dict) -> dict:
        return {k: item[k] for k in pick_keys if item.get(k) not in (None, "")}

    for n, item in enumerate(data.get("main", []), start=1):
        try:
            draft.add_clip(track="V1", role=str(item.get("role", "")), reason=str(item.get("reason", "")), **picks(item))
            labels.append(plan.track("V1").clips[-1].label)
        except PickError as e:
            labels.append(None)
            warnings.append(f"main {n}: {e}")

    def over(item: dict) -> str | None:
        i = int(item.get("over", 0))
        if 1 <= i <= len(labels) and labels[i - 1]:
            return labels[i - 1]
        raise PickError(f"`over` {i} is not a main clip that was made.")

    if profile.broll:
        for n, item in enumerate(data.get("broll", []), start=1):
            try:
                draft.add_clip(track="V2", over=over(item), offset=float(item.get("offset", 0)), role="b-roll",
                               reason=str(item.get("reason", "")), **picks(item))
            except PickError as e:
                warnings.append(f"b-roll {n}: {e}")
    if profile.captions != "none":
        for n, item in enumerate(data.get("captions", []), start=1):
            try:
                draft.add_mark(track="T1", text=str(item["text"]), over=over(item), offset=float(item.get("offset", 0)),
                               duration=float(item.get("duration", 3)))
            except (PickError, KeyError) as e:
                warnings.append(f"caption {n}: {e}")
    music = str(data.get("music", "")).strip()
    if music and plan.track("V1").clips:
        draft.set_music(music)
    if profile.sound_effects:
        for n, item in enumerate(data.get("sound_effects", []), start=1):
            try:
                draft.add_mark(track="A3", text=str(item["text"]), over=over(item), offset=float(item.get("offset", 0)),
                               duration=0.8)
            except (PickError, KeyError) as e:
                warnings.append(f"sound effect {n}: {e}")
    return FirstEdit(plan=plan, warnings=warnings)


class SequenceStage(Stage):
    name = "sequence"

    def run(self, view: str, profile: EditProfile, footage: Footage, max_seconds: int | None = None) -> FirstEdit:
        rep = self.rep
        llm = self.settings.llm
        limit = max_seconds or profile.max_seconds
        key = self.key(view, profile.fingerprint_parts(), limit, llm.model, llm.effort)
        cached = None if self.force else self.cache.load(self.name, key, FirstEdit)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started(f"asking Claude for a first edit ({profile.name})")
        content = [Text("# Profile\n" + profile_text(profile, limit)), Text("# Footage\n" + view)]
        data = self.ctx.llm.generate_json(SEQUENCE_SYSTEM, content, SEQUENCE_SCHEMA, rep.activity)
        result = build_first_edit(data, profile, footage, limit)
        total = result.plan.duration() / 1000
        if limit and total > limit:
            # Claude can't add up cut lengths reliably before the cuts are made; one revision with the real length.
            rep.info(f"first plan is {total:.0f} s, over the {limit} s limit; asking for a shorter one")
            content.append(Text(
                f"# Your previous plan\n{json.dumps(data, ensure_ascii=False)}\n\nCut as planned it lasts {total:.1f} s, "
                f"{total - limit:.1f} s over the {limit} s limit. Return a revised plan that fits: drop or shorten the "
                "weakest parts, keep the hook and the ending."
            ))
            data = self.ctx.llm.generate_json(SEQUENCE_SYSTEM, content, SEQUENCE_SCHEMA, rep.activity)
            result = build_first_edit(data, profile, footage, limit)
            total = result.plan.duration() / 1000
            if total > limit:
                result.warnings.append(f"the edit is {total:.1f} s, still over the {limit} s limit")
        for w in result.warnings:
            rep.warning(w)
        if not result.plan.track("V1").clips:
            rep.warning("Claude's plan produced no main-track clips")
        self.cache.save(self.name, key, result)
        rep.done(f"{len(result.plan.track('V1').clips)} main clips, {result.plan.duration() / 1000:.0f} s")
        return result
