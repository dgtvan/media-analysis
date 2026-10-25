"""Edit profiles: how to analyse and cut one kind of footage (engine/profiles.py). The front matter holds the settings
code uses; the body is guidance for Claude when it plans and changes the edit."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from media_analysis.engine.profiles import Profile, load_profiles, parse_profile, render_profile


class AnalysisSettings(BaseModel):
    words: bool = True
    """Time every word (cuts between words)."""
    verbatim: bool = True
    """Keep fillers so they can be cut."""
    describe: bool = True
    """Claude describes every shot."""


class EditProfile(Profile):
    cutting: Literal["speech", "visual", "screen"] = "speech"
    """What cuts follow: what is said, what is shown, or what changes on screen."""
    analysis: AnalysisSettings = AnalysisSettings()
    target_seconds: int = 60
    max_seconds: int | None = None
    """The longest the edit may be; a project can set its own limit instead."""
    aspect: str = "16:9"
    pacing: Literal["slow", "medium", "fast"] = "medium"
    min_clip_seconds: float = 1.5
    max_clip_seconds: float = 20.0
    structure: list[str] = []
    """Parts of the video in order, e.g. hook, context, body, outro."""
    captions: Literal["none", "key-points", "all-speech"] = "key-points"
    broll: bool = True
    """Lay cutaway footage over speech."""
    music: str = ""
    """Mood of the music placeholder, or empty for none."""
    sound_effects: bool = False


def load_edit_profiles(user_dirs: list[Path]) -> dict[str, EditProfile]:
    return load_profiles("media_analysis.workflows.edit.profiles", user_dirs, EditProfile, "Edit profile")


def parse_edit_profile(text: str, source: str) -> EditProfile:
    return parse_profile(text, source, EditProfile, "Edit profile")


def render_edit_profile(profile: EditProfile, profile_id: str, name: str) -> str:
    """A copy of `profile` under a new id and name, ready to save as a user profile."""
    meta = profile.model_dump(exclude={"instructions", "source", "id", "name"})
    return render_profile({"id": profile_id, "name": name, **meta}, profile.instructions)
