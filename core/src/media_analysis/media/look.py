"""The "look" image (no AI): one picture of a time range for Claude to check a moment without watching the video.

Top: frames spread over the range with their times. Middle: the waveform with quiet stretches shaded. Bottom: the
words at the times they are spoken. Claude asks for it on demand for one range; it is not meant for scanning a
whole recording.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from media_analysis.engine.timecode import format_timecode
from media_analysis.media import ffmpeg
from media_analysis.media.audio import envelope, silences

SAMPLE_RATE = 16000


def look(
    path: Path, start: float, end: float, out: Path, *, has_video: bool = True, has_audio: bool = True,
    words: list[tuple[float, float, str]] | None = None, frames: int = 8, width: int = 1600,
) -> Path:
    end = max(end, start + 0.5)
    span = end - start
    frame_w = width // frames
    frame_h = frame_w * 9 // 16
    wave_h, words_h, label_h = 90, 46, 18
    height = (frame_h + label_h if has_video else 0) + wave_h + words_h
    img = Image.new("RGB", (width, height), (24, 26, 31))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=13)
    x_of = lambda t: int((t - start) / span * width)  # noqa: E731

    y = 0
    if has_video:
        fps = frames / span
        shots = list(ffmpeg.iter_frames(path, fps, frame_w, frame_h, gray=False, start=start, duration=span))[:frames]
        for i, (t, rgb) in enumerate(shots):
            img.paste(Image.fromarray(rgb), (i * frame_w, 0))
            draw.text((i * frame_w + 4, frame_h + 2), format_timecode(t), fill=(200, 205, 214), font=font)
        y = frame_h + label_h

    if has_audio:
        audio = ffmpeg.decode_audio(path, SAMPLE_RATE, start=start, duration=span)
        for a, b in silences(audio, SAMPLE_RATE):
            draw.rectangle([x_of(start + a), y, x_of(start + b), y + wave_h], fill=(46, 52, 64))
        env = envelope(audio, width)
        mid = y + wave_h // 2
        for x, level in enumerate(env):
            half = int(level * (wave_h // 2 - 4))
            draw.line([(x, mid - half), (x, mid + half)], fill=(79, 140, 255))
    else:
        draw.text((8, y + wave_h // 2 - 7), "(no sound)", fill=(139, 147, 161), font=font)
    y += wave_h

    # Words alternate between two rows so neighbours don't overlap.
    for i, (a, _b, text) in enumerate(w for w in (words or []) if start <= w[0] < end):
        row = i % 2
        draw.text((x_of(a), y + 4 + row * 20), text, fill=(230, 232, 236), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out
