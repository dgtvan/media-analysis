from pathlib import Path

import pytest

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.media import ffmpeg


@pytest.fixture
def calls(monkeypatch):
    made: list[list[str]] = []
    monkeypatch.setattr(ffmpeg, "find_binary", lambda name: name)
    return made


def test_a_still_falls_back_to_decoding_from_the_start_when_seeking_gives_no_frame(monkeypatch, calls, tmp_path):
    out = tmp_path / "still.jpg"

    def run(args):
        calls.append(args)
        if args.index("-ss") > args.index("-i"):
            out.write_bytes(b"jpg")
        else:
            raise MediaAnalysisError("ffmpeg.exe failed: Nothing was written into output file")
        return b""

    monkeypatch.setattr(ffmpeg, "_run", run)

    assert ffmpeg.extract_still(Path("share.mp4"), 3.0, out, max_side=640) == out
    assert len(calls) == 2


def test_a_still_found_by_seeking_needs_one_call(monkeypatch, calls, tmp_path):
    out = tmp_path / "still.jpg"

    def run(args):
        calls.append(args)
        out.write_bytes(b"jpg")
        return b""

    monkeypatch.setattr(ffmpeg, "_run", run)

    ffmpeg.extract_still(Path("video.mp4"), 3.0, out)
    assert len(calls) == 1
    assert calls[0].index("-ss") < calls[0].index("-i")
