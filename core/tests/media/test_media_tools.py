import subprocess

import numpy as np
import pytest
from PIL import Image

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.media import ffmpeg
from media_analysis.media.audio import envelope, silences
from media_analysis.media.look import look
from media_analysis.media.shots import ShotParams, detect_shots, find_cuts

SR = 16000


def test_find_cuts_ignores_steady_motion_and_keeps_jumps():
    changes = np.array([0, 5, 6, 5, 6, 40, 5, 6, 5, 5, 6, 5, 5, 6, 45, 5, 6], dtype=float)
    assert find_cuts(changes, ShotParams(fps=8, min_shot=0.5)) == [5, 14]


def test_find_cuts_skips_shots_shorter_than_the_minimum():
    changes = np.array([0, 5, 5, 5, 5, 40, 5, 42, 5, 5, 5, 5, 5], dtype=float)
    assert find_cuts(changes, ShotParams(fps=8, min_shot=0.5)) == [5]


def test_silences_are_found_relative_to_the_speaking_level():
    rng = np.random.default_rng(1)
    speech = rng.normal(0, 0.02, SR * 2).astype(np.float32)  # quiet speaker, about -34 dBFS
    gap = np.zeros(SR, np.float32)
    audio = np.concatenate([speech, gap, speech])
    found = silences(audio, SR)
    assert len(found) == 1 and abs(found[0][0] - 2.0) < 0.1 and abs(found[0][1] - 3.0) < 0.1


def test_envelope_is_normalised():
    audio = np.concatenate([np.zeros(1000), np.ones(1000) * 0.5]).astype(np.float32)
    env = envelope(audio, 4)
    assert env.tolist() == [0.0, 0.0, 1.0, 1.0]


@pytest.fixture(scope="module")
def two_shot_clip(tmp_path_factory):
    try:
        exe = ffmpeg.find_binary("ffmpeg")
    except MediaAnalysisError:
        pytest.skip("ffmpeg not found")
    out = tmp_path_factory.mktemp("media") / "clip.mp4"
    # 3 s of a moving test pattern, then 3 s of a different moving pattern: one cut at 3 s; a tone with a gap.
    subprocess.run([
        exe, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
        "-f", "lavfi", "-i", "mandelbrot=size=320x180:rate=25,trim=duration=3",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=6,volume='if(between(t,2,3),0,1)':eval=frame",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out


def test_detect_shots_finds_the_cut(two_shot_clip):
    shots = detect_shots(two_shot_clip, 6.0)
    assert len(shots) == 2
    assert abs(shots[0].end - 3.0) <= 0.25 and shots[1].end == 6.0
    assert all(s.sharpness > 0 and 0 < s.brightness < 1 for s in shots)


def test_proxy_filmstrip_loudness_and_look(two_shot_clip, tmp_path):
    proxy = ffmpeg.make_proxy(two_shot_clip, tmp_path / "proxy.mp4", height=144)
    info = ffmpeg.probe(proxy)
    assert info["height"] == 144 and info["has_audio"] and abs(info["duration"] - 6.0) < 0.2
    strip = ffmpeg.filmstrip(two_shot_clip, tmp_path / "strip.jpg", 6.0, every=1.0, height=40)
    with Image.open(strip) as im:
        assert im.height == 40 and im.width >= 6 * 60
    assert -40 < ffmpeg.loudness(two_shot_clip) < 0
    out = look(two_shot_clip, 1.0, 5.0, tmp_path / "look.png", words=[(1.2, 1.5, "hello"), (3.5, 3.9, "world")])
    with Image.open(out) as im:
        assert im.width == 1600 and im.height > 200
