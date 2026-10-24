"""Speech-to-text with faster-whisper, on CUDA when available and CPU otherwise."""

from __future__ import annotations

import os
from collections.abc import Callable

import numpy as np

from media_analysis.engine.hardware import Hardware
from media_analysis.engine.settings import USER_MODELS_DIR, TranscriptionSettings

SAMPLE_RATE = 16000


class FasterWhisperTranscriber:
    def __init__(self, settings: TranscriptionSettings, hardware: Hardware) -> None:
        self.settings = settings
        self.device = hardware.device if settings.device == "auto" else settings.device
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self.cpu_threads = hardware.cpu_threads
        self._model = None

    def _load(self):
        if self._model is None:
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from faster_whisper import WhisperModel
            from huggingface_hub.utils import logging as hf_logging

            hf_logging.set_verbosity_error()
            kwargs = dict(
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads if self.device == "cpu" else 0,
            )
            # Use a local copy (shared first, then the user's own) without touching the network. Otherwise download
            # into the shared folder so every account gets it, or into the user's cache if that folder isn't writable.
            shared = str(self.settings.models_dir) if self.settings.models_dir else None
            own = {"download_root": str(USER_MODELS_DIR)} if USER_MODELS_DIR else {}
            attempts = [{"download_root": shared, "local_files_only": True}] if shared else []
            attempts += [{**own, "local_files_only": True}]
            attempts += [{"download_root": shared}] if shared else []
            attempts += [own]
            for i, extra in enumerate(attempts):
                try:
                    self._model = WhisperModel(self.settings.model, **extra, **kwargs)
                    break
                except Exception:
                    if i == len(attempts) - 1:
                        raise
        return self._model

    def transcribe(
        self,
        audio: np.ndarray,
        offset: float,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[str | None, list[tuple[float, float, str]]]:
        """Transcribe one chunk into (start, end, text) segments; times are shifted by `offset` seconds."""
        model = self._load()
        segments, info = model.transcribe(
            audio,
            language=self.settings.language,
            # Re-detects the language every 30 s window, so EN/VI code-switching keeps working.
            multilingual=self.settings.language is None,
            beam_size=self.settings.beam_size,
            vad_filter=True,
            # Feeding previous text back in tends to cause repetition loops on long or mixed-language audio.
            condition_on_previous_text=False,
        )
        out: list[tuple[float, float, str]] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append((offset + seg.start, offset + seg.end, text))
            if on_progress:
                on_progress(seg.end)
        return info.language, out


def split_points(audio: np.ndarray, chunk_seconds: float, search_seconds: float = 20.0) -> list[int]:
    """Sample indices to cut long audio into chunks, nudged to the quietest moment near each target so cuts
    rarely fall mid-word. Returns boundaries including 0 and len(audio)."""
    total = len(audio)
    chunk = int(chunk_seconds * SAMPLE_RATE)
    if total <= chunk * 1.2:
        return [0, total]
    win = SAMPLE_RATE // 10
    n_windows = total // win
    energy = np.sqrt(np.mean(audio[: n_windows * win].reshape(n_windows, win) ** 2, axis=1))
    search = int(search_seconds * 10)
    cuts = [0]
    target = chunk
    while target < total - chunk * 0.2:
        centre = target // win
        lo, hi = max(centre - search, 1), min(centre + search, n_windows - 1)
        best = lo + int(np.argmin(energy[lo:hi])) if hi > lo else centre
        cut = best * win
        if cut > cuts[-1]:
            cuts.append(cut)
        target = cut + chunk
    cuts.append(total)
    return cuts
