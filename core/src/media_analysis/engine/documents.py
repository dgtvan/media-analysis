"""Versioned documents: every change to a document (an edit plan, a report) is kept as a new version with its parent,
who made it and a one-line summary, so any version can be compared with another or made current again.

Layout under the store's root: `<kind>/index.json` (versions and which one is current) and `<kind>/v0001.json`, ...
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from media_analysis.engine.errors import MediaAnalysisError

D = TypeVar("D", bound=BaseModel)


class VersionInfo(BaseModel):
    version: int
    parent: int | None
    author: str
    """Who made it: "analysis", "chat", "user", ..."""
    summary: str
    created: str


class _Index(BaseModel):
    current: int | None = None
    versions: list[VersionInfo] = []


class DocumentStore(Generic[D]):
    def __init__(self, root: Path, kind: str, model: type[D]) -> None:
        self.dir = root / kind
        self.model = model

    def _index_path(self) -> Path:
        return self.dir / "index.json"

    def _read_index(self) -> _Index:
        p = self._index_path()
        return _Index.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else _Index()

    def _write_index(self, index: _Index) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path().with_suffix(".tmp")
        tmp.write_text(index.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self._index_path())

    def _path(self, version: int) -> Path:
        return self.dir / f"v{version:04d}.json"

    def versions(self) -> list[VersionInfo]:
        return self._read_index().versions

    def current(self) -> int | None:
        return self._read_index().current

    def save(self, doc: D, *, author: str, summary: str, parent: int | None = None) -> VersionInfo:
        """Stores `doc` as a new version and makes it current. `parent` defaults to the current version."""
        index = self._read_index()
        number = (index.versions[-1].version if index.versions else 0) + 1
        info = VersionInfo(
            version=number, parent=index.current if parent is None else parent, author=author, summary=summary,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(number).write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        index.versions.append(info)
        index.current = number
        self._write_index(index)
        return info

    def load(self, version: int | None = None) -> D:
        """A version, or the current one."""
        number = self.current() if version is None else version
        if number is None:
            raise MediaAnalysisError(f"No {self.dir.name} has been made yet.")
        path = self._path(number)
        if not path.exists():
            raise MediaAnalysisError(f"{self.dir.name} version {number} doesn't exist.")
        return self.model.model_validate_json(path.read_text(encoding="utf-8"))

    def make_current(self, version: int) -> VersionInfo:
        """Switches to an earlier (or later) version without deleting anything."""
        index = self._read_index()
        info = next((v for v in index.versions if v.version == version), None)
        if info is None:
            raise MediaAnalysisError(f"{self.dir.name} version {version} doesn't exist.")
        index.current = version
        self._write_index(index)
        return info

    def undo(self) -> VersionInfo:
        """Makes the current version's parent current."""
        index = self._read_index()
        cur = next((v for v in index.versions if v.version == index.current), None)
        if cur is None or cur.parent is None:
            raise MediaAnalysisError("There is nothing to undo.")
        return self.make_current(cur.parent)


def diff_ids(before: dict[str, dict], after: dict[str, dict]) -> dict[str, list[str]]:
    """Which item ids were added, removed or changed between two {id: item} maps."""
    return {
        "added": [k for k in after if k not in before],
        "removed": [k for k in before if k not in after],
        "changed": [k for k in after if k in before and json.dumps(after[k], sort_keys=True) != json.dumps(before[k], sort_keys=True)],
    }
