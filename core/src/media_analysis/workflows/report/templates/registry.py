"""Report templates ("processing types"): Markdown files with YAML front matter.

Built-in templates ship with the package; user templates (same format) override built-ins with the same id.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from media_analysis.engine.errors import MediaAnalysisError

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class Template(BaseModel):
    id: str
    name: str
    description: str
    signals: list[str] = []
    evidence: list[str] = []
    sections: list[str]
    fallback: bool = False
    """Used only when no other template fits; never offered to the classifier."""
    instructions: str = ""
    source: str = ""

    def summary_for_classification(self) -> str:
        signals = "; ".join(self.signals) or "-"
        return f"- id: {self.id}\n  name: {self.name}\n  description: {self.description}\n  signals: {signals}"

    def fingerprint_parts(self) -> dict:
        return self.model_dump(exclude={"source"})


def parse_template(text: str, source: str) -> Template:
    m = _FRONT_MATTER.match(text.lstrip("﻿"))
    if not m:
        raise MediaAnalysisError(f"Template {source}: missing YAML front matter between '---' lines.")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
        template = Template(**meta, instructions=m.group(2).strip(), source=source)
    except (yaml.YAMLError, ValidationError, TypeError) as e:
        raise MediaAnalysisError(f"Template {source} is invalid: {e}") from e
    if not ID_PATTERN.match(template.id):
        raise MediaAnalysisError(f"Template {source}: id '{template.id}' must be lowercase letters, digits and '-'.")
    return template


def load_templates(user_dirs: list[Path]) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    builtin = resources.files("media_analysis.workflows.report.templates") / "builtin"
    for entry in sorted(builtin.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            t = parse_template(entry.read_text(encoding="utf-8"), f"built-in:{entry.name}")
            templates[t.id] = t
    for d in user_dirs:
        if d.is_dir():
            for path in sorted(d.glob("*.md")):
                t = parse_template(path.read_text(encoding="utf-8"), str(path))
                templates[t.id] = t
    return templates


SKELETON = """---
id: {id}
name: {name}
description: One sentence describing what kind of recording this template is for.
signals:            # what in the transcript/visuals suggests this type (used to pick the template)
  - ...
evidence:           # what visual moments are worth capturing as frames
  - ...
sections:           # report sections, in order
  - Summary
  - ...
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
        "sections": sections or ["Summary"],
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
