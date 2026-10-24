from pathlib import Path

import numpy as np
import pytest

from media_analysis.core.assemble import assemble_output
from media_analysis.core.cache import Cache, digest
from media_analysis.core.models import (
    Classification, Evidence, EvidenceItem, Ingest, MediaFile, ReportDraft, format_timecode,
)
from media_analysis.errors import MediaAnalysisError
from media_analysis.providers.transcriber import SAMPLE_RATE, split_points
from media_analysis.templates.registry import load_templates, new_template, parse_template


def test_format_timecode():
    assert format_timecode(3725.9) == "01:02:05"
    assert format_timecode(65, "-") == "00-01-05"


def test_digest_is_stable_and_order_independent():
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    draft = ReportDraft(title="t", markdown="# t")
    cache.save("report", "k", draft)
    assert cache.load("report", "k", ReportDraft) == draft
    assert cache.load("report", "missing", ReportDraft) is None


def test_split_points_cut_at_quiet_moments():
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.3, SAMPLE_RATE * 130).astype(np.float32)
    quiet = SAMPLE_RATE * 55
    audio[quiet: quiet + SAMPLE_RATE] = 0
    cuts = split_points(audio, chunk_seconds=60, search_seconds=10)
    assert cuts[0] == 0 and cuts[-1] == len(audio)
    assert quiet <= cuts[1] <= quiet + SAMPLE_RATE


def test_split_points_short_audio_is_one_chunk():
    audio = np.zeros(SAMPLE_RATE * 30, np.float32)
    assert split_points(audio, chunk_seconds=60) == [0, len(audio)]


def test_builtin_templates_load():
    templates = load_templates([])
    assert {"meeting", "technical-discussion", "lecture-tutorial", "interview", "language-practice",
            "casual-chat"} <= set(templates)
    assert all(t.sections for t in templates.values())


def test_user_template_overrides_builtin(tmp_path):
    (tmp_path / "meeting.md").write_text(
        "---\nid: meeting\nname: My meeting\ndescription: d\nsections: [A]\n---\nbody", encoding="utf-8"
    )
    assert load_templates([tmp_path])["meeting"].name == "My meeting"


def test_invalid_template_is_reported():
    with pytest.raises(MediaAnalysisError):
        parse_template("no front matter", "x.md")
    with pytest.raises(MediaAnalysisError):
        parse_template("---\nid: Bad Id\nname: n\ndescription: d\nsections: [A]\n---\n", "x.md")


def test_new_template_skeleton_parses(tmp_path):
    path = new_template("sales-call", tmp_path)
    assert parse_template(path.read_text(encoding="utf-8"), str(path)).id == "sales-call"
    with pytest.raises(MediaAnalysisError):
        new_template("sales-call", tmp_path)


def test_assemble_links_only_referenced_frames(tmp_path):
    frame = tmp_path / "c1.jpg"
    frame.write_bytes(b"jpg")
    unused = tmp_path / "c2.jpg"
    unused.write_bytes(b"jpg")
    ingest = Ingest(files=[MediaFile(id="f1", path="x.mp4", name="My Video.mp4", fingerprint="fp", duration=90,
                                     has_video=True, has_audio=True)])
    evidence = Evidence(items=[
        EvidenceItem(id="E01", file_id="f1", time=75, path=str(frame), caption="Diagram", description="d", text="A -> B"),
        EvidenceItem(id="E02", file_id="f1", time=80, path=str(unused), caption="Other", description="d", text=""),
    ])
    draft = ReportDraft(title="T", markdown="# T\n\n## Summary\n![Arch](evidence:E01)\n![x](evidence:E99)\n")
    template = load_templates([])["meeting"]
    out = tmp_path / "out"
    out.mkdir()
    report, warnings = assemble_output(out, draft, evidence, ingest, template, Classification(
        template_id="meeting", confidence=0.9, reason="r"))
    text = report.read_text(encoding="utf-8")
    assert "![Arch](reference/frames/My-Video_00-01-15.jpg)" in text
    assert "evidence:" not in text
    assert (out / "reference/text/My-Video_00-01-15.md").read_text(encoding="utf-8").count("A -> B") == 1
    assert sorted(p.name for p in (out / "reference/frames").iterdir()) == ["My-Video_00-01-15.jpg"]
    assert any("E99" in w for w in warnings)
