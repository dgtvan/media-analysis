"""Profiles: Markdown files with YAML front matter that tell a workflow how to work on one kind of input.

The front matter holds structured settings a workflow uses directly; the Markdown body is guidance for Claude. Each
workflow defines its own profile model (report templates). Built-in profiles ship with the package;
user profiles in the user's folders replace built-in ones with the same id.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from media_analysis.engine.errors import MediaAnalysisError

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class Profile(BaseModel):
    id: str
    name: str
    description: str
    instructions: str = ""
    """The Markdown body: guidance for Claude."""
    source: str = ""
    """Where it was loaded from ("built-in:<file>" or a path); not part of the profile's content."""

    def fingerprint_parts(self) -> dict:
        return self.model_dump(exclude={"source"})


P = TypeVar("P", bound=Profile)


def parse_profile(text: str, source: str, model: type[P], what: str = "Profile") -> P:
    m = _FRONT_MATTER.match(text.lstrip("﻿"))
    if not m:
        raise MediaAnalysisError(f"{what} {source}: missing YAML front matter between '---' lines.")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
        profile = model(**meta, instructions=m.group(2).strip(), source=source)
    except (yaml.YAMLError, ValidationError, TypeError) as e:
        raise MediaAnalysisError(f"{what} {source} is invalid: {e}") from e
    if not ID_PATTERN.match(profile.id):
        raise MediaAnalysisError(f"{what} {source}: id '{profile.id}' must be lowercase letters, digits and '-'.")
    return profile


def load_profiles(package: str, user_dirs: list[Path], model: type[P], what: str = "Profile") -> dict[str, P]:
    """Built-in profiles from `<package>/builtin/*.md`, then user profiles, later ones replacing earlier ones."""
    profiles: dict[str, P] = {}
    builtin = resources.files(package) / "builtin"
    for entry in sorted(builtin.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            p = parse_profile(entry.read_text(encoding="utf-8"), f"built-in:{entry.name}", model, what)
            profiles[p.id] = p
    for d in user_dirs:
        if d.is_dir():
            for path in sorted(d.glob("*.md")):
                p = parse_profile(path.read_text(encoding="utf-8"), str(path), model, what)
                profiles[p.id] = p
    return profiles


def render_profile(meta: dict, body: str) -> str:
    """A profile file from its front matter and body."""
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{front}---\n{body.strip()}\n"
