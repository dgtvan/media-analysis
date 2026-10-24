"""Run settings. Defaults live here; the CLI overrides them."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# The Windows app passes its data folder (App deployment standard v2.3); the command line defaults to Recording
# Reports' folder, whose install puts the command on PATH.
USER_HOME = (
    Path(os.environ["MEDIA_ANALYSIS_HOME"])
    if os.environ.get("MEDIA_ANALYSIS_HOME")
    else Path(os.environ["LOCALAPPDATA"]) / "VanDang" / "RecordingReports" / "data"
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA")
    else Path.home() / ".media-analysis"
)
USER_TEMPLATES_DIR = USER_HOME / "templates"
USER_MODELS_DIR = USER_HOME / "models" if sys.platform == "win32" else None
"""Where a model goes when the shared folder can't take it (None = Hugging Face's default cache)."""

class TranscriptionSettings(BaseModel):
    model: str = "large-v3"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    language: str | None = Field(None, description="Language hint, e.g. 'en' or 'vi'. None = detect (mixed allowed).")
    models_dir: Path | None = Field(
        default_factory=lambda: Path(d) if (d := os.environ.get("MEDIA_ANALYSIS_MODELS_DIR")) else None,
        description="Shared, read-only model folder (Hugging Face cache layout) checked before the user's own cache.",
    )
    chunk_minutes: float = 10.0
    beam_size: int = 5


class ScanSettings(BaseModel):
    fps: float = 1.0
    width: int = 320
    height: int = 180


class EvidenceSettings(BaseModel):
    max_requests: int = 30
    extract_fps: float = 2.0
    max_candidates_per_request: int = 3
    vision_batch_size: int = 6
    max_image_side: int = 1568


class LlmSettings(BaseModel):
    backend: Literal["subscription", "api"] = "subscription"
    model: str | None = Field(None, description="Claude model id. None = the account's default model.")
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        None, description="Thinking effort. None = the model's default."
    )
    claude_profile: Path | None = Field(
        None, description="Claude Code config folder to sign in with (CLAUDE_CONFIG_DIR). None = Claude Code's default.",
    )
    classify_min_confidence: float = 0.5

    def profile_dir(self) -> Path:
        """The Claude Code config folder that holds the login used for analyses."""
        if self.claude_profile:
            return self.claude_profile
        if env := os.environ.get("CLAUDE_CONFIG_DIR"):
            return Path(env)
        return Path.home() / ".claude"


class Settings(BaseModel):
    transcription: TranscriptionSettings = TranscriptionSettings()
    scan: ScanSettings = ScanSettings()
    evidence: EvidenceSettings = EvidenceSettings()
    llm: LlmSettings = LlmSettings()
    templates_dirs: list[Path] = Field(default_factory=lambda: [USER_TEMPLATES_DIR])
    stats_file: Path | None = Field(
        default_factory=lambda: USER_HOME / "stats" / "runs.jsonl",
        description="Where each run appends one JSON line for statistics (see engine/perf.py). None = don't record.",
    )
