"""Sound measures (no AI): quiet stretches and a waveform envelope from decoded mono audio."""

from __future__ import annotations

import numpy as np


def rms_db(audio: np.ndarray, sample_rate: int, window: float = 0.05) -> np.ndarray:
    """Level in dBFS for each `window` seconds."""
    size = max(1, int(sample_rate * window))
    n = len(audio) // size
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = audio[: n * size].reshape(n, size).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return (20 * np.log10(np.maximum(rms, 1e-6))).astype(np.float32)


def silences(
    audio: np.ndarray, sample_rate: int, *, threshold_db: float = -40.0, relative_db: float = 25.0,
    min_length: float = 0.4, window: float = 0.05,
) -> list[tuple[float, float]]:
    """(start, end) of stretches at least `min_length` seconds long that are quieter than both `threshold_db` and
    `relative_db` below the clip's typical speaking level, so quiet recordings still have their pauses found."""
    levels = rms_db(audio, sample_rate, window)
    if len(levels) == 0:
        return []
    speaking = float(np.percentile(levels, 90))
    limit = min(threshold_db, speaking - relative_db)
    quiet = levels < limit
    out: list[tuple[float, float]] = []
    i = 0
    while i < len(quiet):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < len(quiet) and quiet[j]:
            j += 1
        if (j - i) * window >= min_length:
            out.append((round(i * window, 3), round(j * window, 3)))
        i = j
    return out


def envelope(audio: np.ndarray, points: int) -> np.ndarray:
    """Peak level 0-1 at `points` evenly spaced positions, for drawing a waveform."""
    if len(audio) == 0 or points <= 0:
        return np.zeros(max(points, 0), dtype=np.float32)
    edges = np.linspace(0, len(audio), points + 1).astype(int)
    peaks = np.array([np.abs(audio[a:b]).max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
    top = peaks.max() or 1.0
    return (peaks / top).astype(np.float32)
