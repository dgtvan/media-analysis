"""Report templates ("processing types"): the report workflow's profiles (engine/profiles.py).

Built-in templates ship with the package; user templates (same format) override built-ins with the same id.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.profiles import ID_PATTERN, Profile, load_profiles, parse_profile


class Template(Profile):
    signals: list[str] = []
    evidence: list[str] = []
    sections: list[str]
    fallback: bool = False
    """Used only when no other template fits; never offered to the classifier."""

    def summary_for_classification(self) -> str:
        signals = "; ".join(self.signals) or "-"
        return f"- id: {self.id}\n  name: {self.name}\n  description: {self.description}\n  signals: {signals}"


def parse_template(text: str, source: str) -> Template:
    return parse_profile(text, source, Template, "Template")


def load_templates(user_dirs: list[Path]) -> dict[str, Template]:
    return load_profiles("media_analysis.workflows.report.templates", user_dirs, Template, "Template")


SKELETON = """---
id: {id}
name: {name}
description: One sentence describing what kind of recording this template is for.
signals:            # what in the transcript/visuals suggests this type (used to pick the template)
  - ...
evidence:           # what visual moments are worth capturing as frames
  - ...
sections:           # detail sections, in order; every report starts with a Conclusion (outcome, agreed,
  - ...             #   actions, still open), so don't add summary, decisions, action items or open points
---
Instructions for the report writer: tone, level of detail, what each section must contain,
what to do when information is missing.
"""


def render_template(
    template_id: str, name: str, description: str, signals: list[str], evidence: list[str], sections: list[str],
) -> str:
    """A template file with the given front matter and placeholder instructions, ready to edit."""
    meta = {
        "id": template_id,
        "name": name,
        "description": description,
        "signals": signals or ["..."],
        "evidence": evidence or ["..."],
        "sections": sections or ["Key points"],
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    return (
        f"---\n{front}---\n"
        "Instructions for the report writer: tone, level of detail, what each section must contain,\n"
        "what to do when information is missing.\n"
    )


def new_template(template_id: str, directory: Path) -> Path:
    if not ID_PATTERN.match(template_id):
        raise MediaAnalysisError("Template id must be lowercase letters, digits and '-'.")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{template_id}.md"
    if path.exists():
        raise MediaAnalysisError(f"{path} already exists.")
    name = template_id.replace("-", " ").title()
    path.write_text(SKELETON.format(id=template_id, name=name), encoding="utf-8")
    return path
