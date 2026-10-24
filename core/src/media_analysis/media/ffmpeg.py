"""Thin wrappers around the ffmpeg/ffprobe binaries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import numpy as np

from media_analysis.engine.errors import MediaAnalysisError

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@cache
def find_binary(name: str) -> str:
    """Locate ffmpeg/ffprobe: $MEDIA_ANALYSIS_FFMPEG_DIR, then PATH, then a winget install."""
    env_dir = os.environ.get("MEDIA_ANALYSIS_FFMPEG_DIR")
    if env_dir:
        candidate = Path(env_dir) / (name + (".exe" if sys.platform == "win32" else ""))
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        # winget's PATH entry only reaches shells started after the install.
        packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        for exe in sorted(packages.glob(f"Gyan.FFmpeg*/*/bin/{name}.exe"), reverse=True):
            return str(exe)
    raise MediaAnalysisError(
        f"{name} not found. Install ffmpeg (e.g. `winget install Gyan.FFmpeg`) or set MEDIA_ANALYSIS_FFMPEG_DIR."
    )


def _run(args: list[str]) -> bytes:
    proc = subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise MediaAnalysisError(f"{Path(args[0]).name} failed: " + " | ".join(err))
    return proc.stdout


def version() -> str:
    out = _run([find_binary("ffmpeg"), "-version"]).decode("utf-8", "replace")
    return out.splitlines()[0] if out else "unknown"


def probe(path: Path) -> dict:
    out = _run([
        find_binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path),
    ])
    data = json.loads(out)
    streams = data.get("streams", [])
    # Cover art in audio files shows up as a single-frame video stream.
    video = [
        s for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration") or 0.0)
    width = height = None
    if video:
        width, height = video[0].get("width"), video[0].get("height")
        rotation = _rotation(video[0])
        if rotation in (90, 270) and width and height:
            width, height = height, width
    return {
        "duration": duration,
        "has_video": bool(video),
        "has_audio": bool(audio),
        "width": width,
        "height": height,
    }


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            return abs(int(side["rotation"])) % 360
    tag = stream.get("tags", {}).get("rotate")
    return abs(int(tag)) % 360 if tag else 0


def decode_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Whole audio track as mono float32 at `sample_rate`."""
    out = _run([
        find_binary("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ])
    return np.frombuffer(out, dtype=np.float32).copy()


def iter_frames(
    path: Path,
    fps: float,
    width: int,
    height: int,
    *,
    gray: bool = True,
    start: float = 0.0,
    duration: float | None = None,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (time, frame) at `fps`, letterboxed to width x height so every frame has the same shape."""
    channels = 1 if gray else 3
    pix_fmt = "gray" if gray else "rgb24"
    vf = (
        f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    args = [find_binary("ffmpeg"), "-nostdin", "-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-an", "-vf", vf, "-pix_fmt", pix_fmt, "-f", "rawvideo", "-"]
    frame_bytes = width * height * channels
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    assert proc.stdout is not None
    try:
        i = 0
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            shape = (height, width) if gray else (height, width, 3)
            yield start + i / fps, np.frombuffer(buf, dtype=np.uint8).reshape(shape)
            i += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def extract_still(path: Path, time: float, out: Path, max_side: int | None = None) -> Path:
    """Write one full-quality JPEG at `time` (optionally capped to `max_side` pixels)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [find_binary("ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", f"{time:.3f}", "-i", str(path), "-frames:v", "1"]
    if max_side:
        args += ["-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease"]
    args += ["-q:v", "2", str(out)]
    _run(args)
    if not out.exists():
        raise MediaAnalysisError(f"Could not extract a frame at {time:.1f}s from {path.name}")
    return out
