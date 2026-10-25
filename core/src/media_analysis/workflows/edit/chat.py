"""Changing an edit by chatting: each message is one Claude turn with tools that change a working copy of the
current edit (draft.py). If anything changed, the copy is saved as a new version, so it can be compared with the
previous one or undone. The conversation and a short project memory are kept with the project."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from media_analysis.engine.documents import VersionInfo
from media_analysis.engine.settings import LlmSettings
from media_analysis.providers.agent import AgentTool, ChatEvent, EventSink, ToolError, run_agent
from media_analysis.providers.llm import Text
from media_analysis.workflows.edit.cuts import Footage, PickError
from media_analysis.workflows.edit.draft import Draft
from media_analysis.workflows.edit.models import EditPlan
from media_analysis.workflows.edit.profiles.registry import EditProfile
from media_analysis.workflows.edit.view import plan_view, t
from media_analysis.workflows.edit.workflow import edit_store

CHAT_SYSTEM = """\
You are the editor of a video. The user sees the edit as a preview and a timeline (tracks V1 main, V2 b-roll, T1
captions, A1 dialogue = the sound of V1, A2 music, A3 sound effects; items labelled #1, T1, M1, FX1...) and asks you
for changes in the chat. You change the edit only with the tools.

How to work:
- Call one tool at a time and read its result. Refer to items by their labels and to footage by phrase ids (f1.p12)
  or shot ids (f2#3) from the footage view; use find_footage to look for moments.
- Never invent ids or times. For speech, give phrase ranges and, to start or end inside a phrase, the exact words.
- Respect the length limit when there is one: if a change makes the edit too long, make room elsewhere.
- If the request is unclear or would need footage that doesn't exist, ask instead of guessing.
- When asked to explain or compare, answer without changing anything.
- Use `remember` for lasting preferences the user states ("never use shaky shots").
Finish with a short reply (1-3 sentences) saying what you changed, using the labels."""


class NoArgs(BaseModel):
    pass


class FindFootage(BaseModel):
    query: str = Field(description="Words to look for in the speech and in the shot descriptions.")


class Pick(BaseModel):
    phrase_from: str | None = Field(None, description="First phrase id, e.g. f1.p12 (for speech).")
    phrase_to: str | None = Field(None, description="Last phrase id (default: phrase_from).")
    start_at: str | None = Field(None, description="Start at these exact words of the first phrase.")
    end_after: str | None = Field(None, description="End after these exact words of the last phrase.")
    shot: str | None = Field(None, description="Shot id, e.g. f2#3 (for footage without speech).")
    start: float | None = Field(None, description="Part of the shot: start, in seconds of the clip.")
    end: float | None = Field(None, description="Part of the shot: end, in seconds of the clip.")


class AddClip(Pick):
    track: str = Field("V1", description="V1 (main, in sequence) or a b-roll track such as V2.")
    after: str | None = Field(None, description="V1 only: put it after this label, or 'start'. Default: at the end.")
    over: str | None = Field(None, description="B-roll only: the V1 label it plays over.")
    offset: float = Field(0.0, description="B-roll only: seconds into that V1 clip.")
    role: str = ""
    reason: str = ""


class ReplaceClip(Pick):
    label: str
    reason: str = ""


class TrimClip(BaseModel):
    label: str
    start_at: str | None = Field(None, description="Start at these exact words.")
    end_after: str | None = Field(None, description="End after these exact words.")
    trim_start: float = Field(0.0, description="Seconds to cut from the start (negative = extend).")
    trim_end: float = Field(0.0, description="Seconds to cut from the end (negative = extend).")


class MoveClip(BaseModel):
    label: str
    after: str | None = Field(None, description="V1: after this label, or 'start'; default: the end.")
    over: str | None = Field(None, description="Other tracks: the V1 label it plays over.")
    offset: float = 0.0


class Label(BaseModel):
    label: str


class AddMark(BaseModel):
    kind: Literal["caption", "sound_effect"]
    text: str
    over: str = Field(description="The V1 label it belongs to.")
    offset: float = Field(0.0, description="Seconds into that clip.")
    duration: float = Field(3.0, description="Seconds on screen (captions) or length (sound effects).")


class SetText(BaseModel):
    label: str
    text: str
    duration: float | None = None


class SetMusic(BaseModel):
    text: str = Field(description="What music to use, e.g. 'calm piano, ducked under speech'.")


class MergeTracks(BaseModel):
    source: str
    into: str


class SplitTrack(BaseModel):
    track: str
    labels: list[str]
    name: str = Field(description="Name of the new track.")


class RenameTrack(BaseModel):
    track: str
    name: str


class Remember(BaseModel):
    note: str = Field(description="A lasting preference or fact about this project, one sentence.")


def search(footage: Footage, descriptions: dict[str, str], query: str, limit: int = 20) -> str:
    words = [w for w in query.lower().split() if len(w) > 1]
    if not words:
        raise ToolError("Give some words to look for.")
    hits: list[tuple[int, str]] = []
    for phrases in footage.phrases.values():
        for p in phrases:
            score = sum(w in p.text.lower() for w in words)
            if score:
                hits.append((score, f"{p.id} {t(p.start)}-{t(p.end)} {p.text}"))
    for shot_id, text in descriptions.items():
        score = sum(w in text.lower() for w in words)
        if score:
            hits.append((score, f"{shot_id} {text}"))
    hits.sort(key=lambda h: -h[0])
    return "\n".join(h for _, h in hits[:limit]) or "Nothing found."


class EditChat:
    def __init__(
        self, project_dir: Path, footage: Footage, footage_text: str, profile: EditProfile,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self.dir = project_dir
        self.footage = footage
        self.footage_text = footage_text
        self.profile = profile
        self.descriptions = descriptions or {}
        self.store = edit_store(project_dir)
        self.chat_dir = project_dir / "chat"

    # --- history and memory ---------------------------------------------------------------------------------------

    def history(self) -> list[dict]:
        path = self.chat_dir / "history.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append(self, entry: dict) -> None:
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        with (self.chat_dir / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def memory(self) -> str:
        path = self.chat_dir / "memory.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _remember(self, note: str) -> str:
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        with (self.chat_dir / "memory.md").open("a", encoding="utf-8") as f:
            f.write(f"- {note.strip()}\n")
        return "Noted."

    # --- tools ----------------------------------------------------------------------------------------------------

    def tools(self, draft: Draft) -> list[AgentTool]:
        def guard(fn):
            def run(args):
                try:
                    return fn(args)
                except PickError as e:
                    raise ToolError(str(e)) from None
            return run

        def pick(a: Pick) -> dict:
            return {k: v for k, v in a.model_dump().items() if k in Pick.model_fields and v not in (None, "")}

        return [
            AgentTool("find_footage", "Search the speech and the shot descriptions; returns ids with times.", FindFootage,
                      lambda a: search(self.footage, self.descriptions, a.query)),
            AgentTool("show_edit", "The current edit, track by track.", NoArgs,
                      lambda a: plan_view(draft.plan, self.footage.ingest)),
            AgentTool("add_clip", "Add footage: on V1 in sequence (after), or as b-roll over a V1 clip (over, offset).",
                      AddClip, guard(lambda a: draft.add_clip(
                          track=a.track, after=a.after, over=a.over, offset=a.offset, role=a.role, reason=a.reason, **pick(a)))),
            AgentTool("replace_clip", "Use other footage for an existing clip, keeping its place.", ReplaceClip,
                      guard(lambda a: draft.replace_clip(a.label, reason=a.reason, **pick(a)))),
            AgentTool("trim_clip", "Shorten or extend a clip: start/end at given words, or by seconds.", TrimClip,
                      guard(lambda a: draft.trim_clip(a.label, start_at=a.start_at, end_after=a.end_after,
                                                      trim_start=a.trim_start, trim_end=a.trim_end))),
            AgentTool("move_clip", "Move a clip: V1 to after another label; others over another V1 clip.", MoveClip,
                      guard(lambda a: draft.move_clip(a.label, after=a.after, over=a.over, offset=a.offset))),
            AgentTool("remove", "Remove a clip, caption, music or sound effect by its label.", Label,
                      guard(lambda a: draft.remove(a.label))),
            AgentTool("add_mark", "Add a caption (T1) or a sound effect placeholder (A3) over a V1 clip.", AddMark,
                      guard(lambda a: draft.add_mark(track="T1" if a.kind == "caption" else "A3", text=a.text,
                                                     over=a.over, offset=a.offset, duration=a.duration))),
            AgentTool("set_text", "Change the text (and optionally duration) of a caption, music or sound effect.", SetText,
                      guard(lambda a: draft.set_text(a.label, a.text, a.duration))),
            AgentTool("set_music", "Set the music placeholder for the whole edit.", SetMusic,
                      guard(lambda a: draft.set_music(a.text))),
            AgentTool("merge_tracks", "Move everything from one track into another and remove the first.", MergeTracks,
                      guard(lambda a: draft.merge_tracks(a.source, a.into))),
            AgentTool("split_track", "Move some items of a track to a new track.", SplitTrack,
                      guard(lambda a: draft.split_track(a.track, a.labels, a.name))),
            AgentTool("rename_track", "Rename a track.", RenameTrack, guard(lambda a: draft.rename_track(a.track, a.name))),
            AgentTool("remember", "Keep a lasting preference for this project.", Remember, lambda a: self._remember(a.note)),
        ]

    # --- a turn ---------------------------------------------------------------------------------------------------

    def send(self, message: str, llm: LlmSettings, on_event: EventSink | None = None) -> tuple[str, VersionInfo | None]:
        current = self.store.current()
        plan = self.store.load()
        draft = Draft(EditPlan.model_validate(plan.model_dump()), self.footage)
        recent = self.history()[-12:]
        limit = plan.format.max_seconds
        context = [
            Text(f"# Profile\n{self.profile.name}: {self.profile.instructions}\n"
                 + (f"Length limit: {limit} s. " if limit else "") + f"Target: {plan.format.target_seconds} s."),
            Text("# Project memory\n" + (self.memory() or "(nothing yet)")),
            Text("# Footage\n" + self.footage_text),
            Text("# Current edit\n" + plan_view(plan, self.footage.ingest)),
            Text("# Conversation so far\n" + ("\n".join(f"{m['role']}: {m['text']}" for m in recent) or "(none)")),
            Text("# Request\n" + message),
        ]
        self._append({"role": "user", "text": message})
        result = run_agent(
            system=CHAT_SYSTEM, content=context, tools=self.tools(draft), backend=llm.backend, model=llm.model,
            effort=llm.effort, profile=llm.claude_profile, on_event=on_event,
        )
        info = None
        if draft.changes:
            summary = draft.changes[0] if len(draft.changes) == 1 else f"{len(draft.changes)} changes: " + "; ".join(draft.changes)
            info = self.store.save(draft.plan, author="chat", summary=summary[:300], parent=current)
            if on_event:
                on_event(ChatEvent("edit.version", {"version": info.version, "summary": info.summary}))
        self._append({"role": "assistant", "text": result.text, "version": info.version if info else None,
                      "changes": draft.changes})
        return result.text, info
