"""Content-addressed cache for stage artifacts, so reruns skip finished work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


def digest(parts: Any) -> str:
    """Stable short hash of any JSON-serialisable value (pydantic models allowed)."""

    def default(o: Any) -> Any:
        if isinstance(o, BaseModel):
            return o.model_dump(mode="json")
        if isinstance(o, Path):
            return str(o)
        raise TypeError(f"not serialisable: {type(o)}")

    raw = json.dumps(parts, sort_keys=True, default=default, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def file_fingerprint(path: Path, sample: int = 8 * 1024 * 1024) -> str:
    """Fast fingerprint of a large media file: size + head + tail, not the whole content."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(sample))
        if size > sample:
            f.seek(max(size - sample, sample))
            h.update(f.read(sample))
    return h.hexdigest()[:16]


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def dir(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def path(self, stage: str, key: str, suffix: str = ".json") -> Path:
        return self.dir(stage) / f"{key}{suffix}"

    def load(self, stage: str, key: str, model: type[M]) -> M | None:
        p = self.path(stage, key)
        if not p.exists():
            return None
        try:
            return model.model_validate_json(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def save(self, stage: str, key: str, value: BaseModel) -> Path:
        p = self.path(stage, key)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(value.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(p)
        return p
