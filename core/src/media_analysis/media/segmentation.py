"""Visual segmentation (no AI): splits a video into stretches whose content stays the same.

Comparisons are structural rather than pixel-by-pixel: frames are blurred to drop noise and compression artifacts,
isolated changed specks are removed, regions that keep changing (clocks, webcam tiles, playing video) are masked out,
and a change only counts when enough of the picture's area or edges differ for a sustained moment.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from media_analysis.media import ffmpeg
from media_analysis.media.frames import blur, changed_pixels, dhash, edges, hamming, sharpness

_KERNEL = np.ones((3, 3), np.uint8)


@dataclass
class ScanParams:
    pixel_threshold: int = 25
    """Grey-level difference (after blur) for a pixel to count as changed."""
    block: int = 10
    volatile_window: int = 10
    """Frames either side used to decide whether a region keeps changing."""
    volatile_freq: float = 0.3
    moving_threshold: float = 0.01
    """Share of the frame changing between consecutive frames above which the picture counts as moving."""
    moving_min_run: int = 3
    cut_threshold: float = 0.5
    """Share of the frame changing at once that always starts a new segment."""
    area_threshold: float = 0.01
    edge_abs_threshold: float = 0.003
    edge_rel_threshold: float = 0.10
    hash_threshold: int = 14
    persistence: int = 2
    settle_threshold: float = 0.005
    cut_correlation: float = 0.7


@dataclass
class RawSegment:
    start_index: int
    end_index: int
    kind: str
    edge_density: float
    rep_index: int


def scan_video(
    path: Path,
    duration: float,
    fps: float,
    width: int,
    height: int,
    params: ScanParams | None = None,
    on_progress: Callable[[float, float], None] | None = None,
) -> list[tuple[float, float, str, float, float]]:
    """Split a video into visual segments. Returns (start, end, kind, edge_density, rep_time) tuples."""
    p = params or ScanParams()
    capacity = int(duration * fps) + 8
    with tempfile.TemporaryDirectory(prefix="media-analysis-scan-") as tmp:
        # Disk-backed so hour-long videos don't need hundreds of MB of RAM.
        frames = np.lib.format.open_memmap(
            Path(tmp) / "frames.npy", mode="w+", dtype=np.uint8, shape=(capacity, height, width)
        )
        bh, bw = height // p.block, width // p.block
        motion: list[float] = []
        block_changes: list[np.ndarray] = []
        hists: list[np.ndarray] = []
        prev_blur: np.ndarray | None = None
        n = 0
        for _, frame in ffmpeg.iter_frames(path, fps, width, height, gray=True):
            if n >= capacity:
                break
            frames[n] = frame
            b = blur(frame)
            if prev_blur is None:
                motion.append(0.0)
                block_changes.append(np.zeros((bh, bw), dtype=bool))
            else:
                changed = changed_pixels(b, prev_blur, p.pixel_threshold)
                motion.append(float(changed.mean()))
                cropped = changed[: bh * p.block, : bw * p.block]
                block_changes.append(cropped.reshape(bh, p.block, bw, p.block).any(axis=(1, 3)))
            hist = cv2.calcHist([frame], [0], None, [32], [0, 256])
            hists.append(cv2.normalize(hist, hist).flatten())
            prev_blur = b
            n += 1
            if on_progress and n % 30 == 0:
                on_progress(min(n / fps, duration), duration)
        if n == 0:
            return []
        frames.flush()
        raw = segment_frames(frames[:n], np.array(motion), np.array(block_changes), hists, p)
        del frames
    to_time = lambda i: min(i / fps, duration)  # noqa: E731
    return [
        (to_time(s.start_index), to_time(s.end_index + 1), s.kind, s.edge_density, to_time(s.rep_index))
        for s in raw
    ]


def frame_kinds(motion: np.ndarray, p: ScanParams) -> list[str]:
    """'moving' for runs of at least `moving_min_run` frames with motion, otherwise 'static'.
    A single changed frame (e.g. a slide transition) stays static, and a cut frame belongs to what follows it."""
    moving = (motion > p.moving_threshold) & (motion <= p.cut_threshold)
    kinds = ["static"] * len(motion)
    i = 0
    while i < len(motion):
        if not moving[i]:
            i += 1
            continue
        j = i
        while j < len(motion) and moving[j]:
            j += 1
        if j - i >= p.moving_min_run:
            kinds[i:j] = ["moving"] * (j - i)
        i = j
    return kinds


def _volatile_masks(block_changes: np.ndarray, p: ScanParams) -> np.ndarray:
    """Per frame, the blocks that changed in at least `volatile_freq` of the surrounding frames."""
    n = len(block_changes)
    csum = np.concatenate([np.zeros((1, *block_changes.shape[1:])), np.cumsum(block_changes, axis=0)])
    idx = np.arange(n)
    lo = np.clip(idx - p.volatile_window, 0, n)
    hi = np.clip(idx + p.volatile_window + 1, 0, n)
    freq = (csum[hi] - csum[lo]) / (hi - lo)[:, None, None]
    return np.array([cv2.dilate((f >= p.volatile_freq).astype(np.uint8), _KERNEL) > 0 for f in freq])


def _expand(block_mask: np.ndarray, block: int, shape: tuple[int, int]) -> np.ndarray:
    full = np.zeros(shape, dtype=bool)
    up = np.kron(block_mask, np.ones((block, block), dtype=bool))
    full[: up.shape[0], : up.shape[1]] = up
    return full


def segment_frames(
    frames: np.ndarray, motion: np.ndarray, block_changes: np.ndarray, hists: list[np.ndarray], p: ScanParams,
) -> list[RawSegment]:
    """Find segment boundaries.

    - A switch between static and moving, or a frame where most of the picture changes at once, always splits.
    - Moving stretches split only on hard cuts (brightness histogram jump), so people moving never count.
    - Static stretches split when the current frame differs from the segment's anchor frame (not just the previous
      frame, so slow changes add up) for `persistence` frames in a row, ignoring regions that keep changing.
    """
    n, h, w = frames.shape
    if n > 1:
        # The first frame has no predecessor; borrow the second frame's motion so it joins that segment.
        motion = motion.copy()
        motion[0] = motion[1]
    kinds = frame_kinds(motion, p)
    vol_blocks = _volatile_masks(block_changes, p)

    def is_changed(anchor_i: int, i: int) -> bool:
        keep = ~_expand(vol_blocks[i], p.block, (h, w))
        a, b = blur(frames[anchor_i]), blur(frames[i])
        usable = max(int(keep.sum()), 1)
        if changed_pixels(a, b, p.pixel_threshold, keep).sum() / usable > p.area_threshold:
            return True
        ea, eb = edges(a) & keep, edges(b) & keep
        new = eb & ~(cv2.dilate(ea.astype(np.uint8), _KERNEL) > 0)
        lost = ea & ~(cv2.dilate(eb.astype(np.uint8), _KERNEL) > 0)
        moved = int(new.sum() + lost.sum())
        total = max(int(ea.sum() + eb.sum()), 1)
        if moved / usable > p.edge_abs_threshold and moved / total > p.edge_rel_threshold:
            return True
        return bool(keep.mean() > 0.95 and hamming(dhash(a), dhash(b)) > p.hash_threshold)

    def settled(i: int) -> bool:
        return i + 1 >= n or motion[i + 1] < p.settle_threshold

    boundaries = [0]
    anchor, is_settled, pending = 0, settled(0), None
    for i in range(1, n):
        if kinds[i] != kinds[i - 1] or motion[i] > p.cut_threshold:
            boundaries.append(i)
            anchor, is_settled, pending = i, settled(i), None
            continue
        if kinds[i] == "moving":
            if cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_CORREL) < p.cut_correlation:
                boundaries.append(i)
            continue
        if not is_settled:
            # Mid-transition (fade, short scroll): move the anchor until the picture holds still.
            anchor, is_settled = i, settled(i)
            continue
        if is_changed(anchor, i):
            if pending is None:
                pending = i
            if i - pending + 1 >= p.persistence:
                boundaries.append(pending)
                anchor, is_settled, pending = i, settled(i), None
        else:
            pending = None

    segments: list[RawSegment] = []
    bounds = boundaries + [n]
    for s, e in zip(bounds[:-1], bounds[1:]):
        if e <= s:
            continue
        sample = list(range(s, e))[:: max(1, (e - s) // 20)]
        kind = "moving" if kinds[s:e].count("moving") * 2 > (e - s) else "static"
        density = float(np.mean([edges(blur(frames[i])).mean() for i in sample]))
        rep = max(sample, key=lambda i: sharpness(frames[i]))
        segments.append(RawSegment(s, e - 1, kind, density, rep))
    return _merge_short(segments)


def _merge_short(segments: list[RawSegment], min_frames: int = 2) -> list[RawSegment]:
    merged: list[RawSegment] = []
    for seg in segments:
        short = seg.end_index - seg.start_index + 1 < min_frames
        if merged and short:
            merged[-1].end_index = seg.end_index
        else:
            merged.append(seg)
    return merged

