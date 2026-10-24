"""Generate a short synthetic "technical meeting" video for end-to-end testing.

Speech comes from Windows text-to-speech (System.Speech), so this script runs on Windows only.
The video has a camera-like section (moving shapes), a diagram slide, a code slide that gains one line midway,
and an action-items slide, with a ticking clock in the corner to exercise the volatile-region masking.

Usage: uv run --project core python core/tests/fixtures/make_sample.py <output-folder>
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from media_analysis.media.ffmpeg import find_binary

W, H, FPS = 1280, 720, 10

SECTIONS = [
    ("camera", "Good morning everyone. Thanks for joining. Today we will review the architecture of the new login "
               "service, and agree on the next steps before the release."),
    ("diagram", "As you can see on this diagram, the web client calls the A P I gateway. The gateway forwards login "
                "requests to the auth service, and the auth service reads user records from the Postgres database. "
                "Tokens are cached in Redis for fifteen minutes."),
    ("code", "Now let's look at the code. This function validates the token. First we decode it, then we check the "
             "expiry. I just added one more line that checks the issuer, which was missing before."),
    ("actions", "So, the action items. Anna will add rate limiting to the gateway by Friday. Ben will update the A P I "
                "documentation next week. Any questions? No? Great, thanks everyone."),
]

CODE_LINES = [
    "def validate_token(token: str) -> Claims:",
    "    claims = jwt.decode(token, KEY)",
    "    if claims.exp < now():",
    "        raise Expired()",
]
EXTRA_LINE = "    if claims.iss != ISSUER: raise BadIssuer()"


def tts(text: str, out: Path) -> float:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{out}'); $s.Speak('{text.replace(chr(39), chr(39) * 2)}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("consola.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def slide(kind: str, t: float, local_t: float, section_len: float) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    if kind == "camera":
        img = Image.new("RGB", (W, H), (60, 70, 90))
        d = ImageDraw.Draw(img)
        x = 640 + 120 * np.sin(t * 1.3)
        y = 360 + 40 * np.sin(t * 2.1)
        d.ellipse((x - 120, y - 150, x + 120, y + 150), fill=(220, 180, 150))
        d.rectangle((x - 200, y + 150, x + 200, H), fill=(40, 90, 160))
        mouth = 10 + 15 * abs(np.sin(t * 9))
        d.ellipse((x - 40, y + 60, x + 40, y + 60 + mouth), fill=(120, 40, 40))
    elif kind == "diagram":
        d.text((60, 40), "Login service architecture", fill="black", font=font(44))
        boxes = {"Web client": (80, 300), "API Gateway": (380, 300), "Auth service": (700, 300),
                 "Postgres DB": (1000, 200), "Redis cache": (1000, 420)}
        for label, (bx, by) in boxes.items():
            d.rectangle((bx, by, bx + 220, by + 100), outline="black", width=4)
            d.text((bx + 20, by + 35), label, fill="black", font=font(28))
        for (x1, y1), (x2, y2) in [((300, 350), (380, 350)), ((600, 350), (700, 350)),
                                   ((920, 330), (1000, 250)), ((920, 370), (1000, 470))]:
            d.line((x1, y1, x2, y2), fill="black", width=4)
    elif kind == "code":
        d.text((60, 40), "token.py", fill="black", font=font(40))
        lines = CODE_LINES + ([EXTRA_LINE] if local_t > section_len * 0.6 else [])
        for i, line in enumerate(lines):
            d.text((80, 160 + i * 50), f"{i + 1:>2}  {line}", fill=(20, 20, 120), font=font(30))
    else:
        d.text((60, 40), "Action items", fill="black", font=font(44))
        for i, line in enumerate(["Anna: add rate limiting to the gateway (Friday)",
                                  "Ben: update the API documentation (next week)"]):
            d.text((80, 180 + i * 70), f"- {line}", fill="black", font=font(32))
    if kind != "camera":
        d.text((W - 150, H - 50), f"10:{int(t) // 60:02d}:{int(t) % 60:02d}", fill=(120, 120, 120), font=font(24))
    return img


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_binary("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        timeline, wavs, t0 = [], [], 0.0
        for i, (kind, text) in enumerate(SECTIONS):
            wav = tmpd / f"s{i}.wav"
            dur = tts(text, wav) + 1.0
            timeline.append((kind, t0, dur))
            wavs.append(wav)
            t0 += dur
        concat = tmpd / "list.txt"
        audio = tmpd / "speech.wav"
        # apad gives each section one second of trailing silence to match the slide timing.
        parts = []
        for i, w in enumerate(wavs):
            padded = tmpd / f"p{i}.wav"
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(w), "-af", "apad=pad_dur=1", str(padded)], check=True)
            parts.append(padded)
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), str(audio)],
                       check=True)

        out = out_dir / "sample-meeting.mp4"
        proc = subprocess.Popen(
            [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
             "-i", "-", "-i", str(audio), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(out)],
            stdin=subprocess.PIPE,
        )
        assert proc.stdin is not None
        for kind, start, dur in timeline:
            for k in range(int(dur * FPS)):
                local_t = k / FPS
                proc.stdin.write(slide(kind, start + local_t, local_t, dur).tobytes())
        proc.stdin.close()
        proc.wait()
    print(out)
    for kind, start, dur in timeline:
        print(f"{kind:8s} {start:6.1f}s - {start + dur:6.1f}s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
