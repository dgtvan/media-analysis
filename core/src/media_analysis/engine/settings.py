"""Run settings. Defaults live here; the CLI overrides them."""

from __future__ import annotations

import hashlib
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

AZURE_KEY_ENV = "MEDIA_ANALYSIS_AZURE_KEY"
"""Environment variable holding the Azure AI Speech key. Kept out of the settings so it never reaches logs or run records."""
TERMS_FILE_NAME = "transcription-terms.txt"


def read_terms(path: Path | None) -> list[str]:
    """Terms from a text file: one per line; blank lines and lines starting with # are skipped. Missing file = none."""
    if path is None or not path.is_file():
        return []
    terms = (line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines())
    return list(dict.fromkeys(t for t in terms if t and not t.startswith("#")))


class TranscriptionSettings(BaseModel):
    engine: Literal["local", "azure-speech"] = Field(
        "local", description="local = faster-whisper on this computer; azure-speech = Azure AI Speech fast transcription.",
    )
    azure_endpoint: str | None = Field(
        None, description="The Azure AI Speech (or AI Services) resource endpoint, e.g. https://<name>.services.ai.azure.com/.",
    )
    terms_file: Path | None = Field(
        default_factory=lambda: USER_HOME / TERMS_FILE_NAME,
        description="Technical terms and names the cloud engine should expect, one per line (Azure AI Speech only).",
    )
    model: str = "large-v3"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    language: str | None = Field(None, description="Language hint, e.g. 'en' or 'vi'. None = detect (mixed allowed).")
    models_dir: Path | None = Field(
        default_factory=lambda: Path(d) if (d := os.environ.get("MEDIA_ANALYSIS_MODELS_DIR")) else None,
        description="Shared, read-only model folder (Hugging Face cache layout) checked before the user's own cache.",
    )
    chunk_minutes: float = 10.0
    beam_size: int = 5
    word_timestamps: bool = Field(False, description="Also time every word (for cutting between words).")
    verbatim: bool = Field(False, description="Keep fillers such as 'um' and 'uh' instead of tidying them away.")

    def cache_key(self) -> dict:
        """What a transcript depends on. Options that are off are left out, so turning on an option a workflow needs
        doesn't redo transcripts made without it."""
        if self.engine == "local":
            # Unchanged from before engines could be chosen, so existing transcripts stay valid.
            key = self.model_dump(exclude={"engine", "azure_endpoint", "terms_file", "device", "word_timestamps", "verbatim"})
        else:
            # Which resource serves it doesn't change the transcript; the terms do, but not where they are kept.
            key = {"engine": self.engine, "language": self.language, "chunk_minutes": self.chunk_minutes}
            if terms := read_terms(self.terms_file):
                key["terms"] = hashlib.sha256("\n".join(terms).encode()).hexdigest()[:16]
        key.update({k: True for k in ("word_timestamps", "verbatim") if getattr(self, k)})
        return key


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
    transcript_budget_chars: int = Field(
        300_000, description="Longer transcript views are condensed in parts before the Claude calls that read them.",
    )

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
    correct_transcript: bool = Field(
        False, description="Have Claude fix clearly misheard words in the transcript before the report reads it.",
    )
    resource_use: str | None = Field(
        None, description="How much of the computer the caller let the run use, in its words (recorded only).",
    )
    stats_file: Path | None = Field(
        default_factory=lambda: USER_HOME / "stats" / "runs.jsonl",
        description="Where each run appends one JSON line for statistics (see engine/perf.py). None = don't record.",
    )
