"""Shot detection for camera footage (no AI): where a clip cuts from one shot to another, and how usable each shot
is (sharpness, shake, brightness).

Screen recordings use the structural scan in segmentation.py instead; camera footage moves all the time, so here a
cut is a jump in colour content that stands out from the surrounding frames, the approach PySceneDetect's adaptive
detector takes. Phone clips usually hold one shot, so most clips come out as a single shot with its quality measures.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from media_analysis.media import ffmpeg
from media_analysis.media.frames import sharpness


@dataclass
class ShotParams:
    fps: float = 8.0
    width: int = 256
    height: int = 144
    min_content: float = 15.0
    """A cut needs at least this colour change (mean over hue, saturation, value; 0-255)."""
    adaptive_ratio: float = 3.0
    """...and this many times the average change of the frames around it, so steady motion isn't a cut."""
    window: int = 4
    """Frames on each side used for the average."""
    min_shot: float = 0.8
    """Shortest shot in seconds."""


@dataclass
class RawShot:
    start: float
    end: float
    sharpness: float
    """Mean variance of the Laplacian (higher is sharper)."""
    shake: float
    """Mean frame-to-frame global movement in pixels at the analysis size (higher is shakier)."""
    brightness: float
    """Mean luma 0-1."""
    clipped: float
    """Share of pixels that are nearly black or nearly white."""
    rep_time: float
    """Time of the sharpest frame."""


def content_change(a_hsv: np.ndarray, b_hsv: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(a_hsv, b_hsv)))


def find_cuts(changes: np.ndarray, p: ShotParams) -> list[int]:
    """Frame indexes where a new shot starts (excluding 0)."""
    cuts: list[int] = []
    min_gap = max(1, int(p.min_shot * p.fps))
    for i in range(1, len(changes)):
        c = changes[i]
        if c < p.min_content:
            continue
        lo, hi = max(1, i - p.window), min(len(changes), i + p.window + 1)
        around = [changes[j] for j in range(lo, hi) if j != i]
        avg = float(np.mean(around)) if around else 0.0
        if avg > 0 and c / avg < p.adaptive_ratio:
            continue
        if (cuts and i - cuts[-1] < min_gap) or i < min_gap:
            continue
        cuts.append(i)
    return cuts


def detect_shots(
    path: Path, duration: float, params: ShotParams | None = None,
    on_progress: Callable[[float, float], None] | None = None,
) -> list[RawShot]:
    p = params or ShotParams()
    changes: list[float] = []
    shakes: list[float] = []
    sharp: list[float] = []
    bright: list[float] = []
    clipped: list[float] = []
    prev_hsv = prev_gray = None
    n = 0
    for _, rgb in ffmpeg.iter_frames(path, p.fps, p.width, p.height, gray=False):
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        changes.append(0.0 if prev_hsv is None else content_change(prev_hsv, hsv))
        shakes.append(0.0 if prev_gray is None else float(np.hypot(*cv2.phaseCorrelate(prev_gray, gray)[0])))
        sharp.append(sharpness(gray.astype(np.uint8)))
        bright.append(float(gray.mean() / 255))
        clipped.append(float(((gray < 8) | (gray > 247)).mean()))
        prev_hsv, prev_gray = hsv, gray
        n += 1
        if on_progress and n % 40 == 0:
            on_progress(min(n / p.fps, duration), duration)
    if n == 0:
        return []
    bounds = [0, *find_cuts(np.array(changes), p), n]
    shots: list[RawShot] = []
    to_time = lambda i: min(i / p.fps, duration)  # noqa: E731
    for s, e in zip(bounds[:-1], bounds[1:]):
        # The first frame of a shot has the jump from the previous shot in its shake value; leave it out.
        inner = range(s + 1, e) if e - s > 1 else range(s, e)
        rep = max(range(s, e), key=lambda i: sharp[i])
        shots.append(RawShot(
            start=to_time(s), end=to_time(e) if e < n else duration,
            sharpness=float(np.mean([sharp[i] for i in range(s, e)])),
            shake=float(np.mean([shakes[i] for i in inner])) if inner else 0.0,
            brightness=float(np.mean([bright[i] for i in range(s, e)])),
            clipped=float(np.mean([clipped[i] for i in range(s, e)])),
            rep_time=to_time(rep),
        ))
    return shots
