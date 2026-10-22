"""Run settings. Defaults live here; the CLI overrides them."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

USER_HOME = (
    Path(os.environ["APPDATA"]) / "MediaAnalysis"
    if sys.platform == "win32" and os.environ.get("APPDATA")
    else Path.home() / ".media-analysis"
)
USER_TEMPLATES_DIR = USER_HOME / "templates"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr"}


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
    classify_min_confidence: float = 0.5


class Settings(BaseModel):
    transcription: TranscriptionSettings = TranscriptionSettings()
    scan: ScanSettings = ScanSettings()
    evidence: EvidenceSettings = EvidenceSettings()
    llm: LlmSettings = LlmSettings()
    templates_dirs: list[Path] = Field(default_factory=lambda: [USER_TEMPLATES_DIR])
