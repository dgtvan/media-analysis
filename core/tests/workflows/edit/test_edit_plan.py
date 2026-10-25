import json

import pytest

from media_analysis.engine.documents import DocumentStore
from media_analysis.understand.models import Ingest, MediaFile, Shot, ShotList, Word
from media_analysis.understand.views import Phrase
from media_analysis.workflows.edit.cuts import Footage, PickError, speech_cut
from media_analysis.workflows.edit.draft import Draft, empty_plan
from media_analysis.workflows.edit.export import edit_json, edit_markdown
from media_analysis.workflows.edit.models import EditFormat, EditPlan
from media_analysis.workflows.edit.profiles.registry import load_edit_profiles
from media_analysis.workflows.edit.sequence import build_first_edit


def words(start, text, step=0.4):
    return [Word(start=start + i * step, end=start + i * step + 0.3, text=w) for i, w in enumerate(text.split())]


@pytest.fixture
def footage():
    ingest = Ingest(files=[
        MediaFile(id="f1", path="talk.mp4", name="talk.mp4", fingerprint="a", duration=30, has_video=True, has_audio=True),
        MediaFile(id="f2", path="forest.mp4", name="forest.mp4", fingerprint="b", duration=20, has_video=True, has_audio=False),
    ])
    p1 = words(1.0, "um so today we climb the mountain")
    p2 = words(6.0, "it was a long day but worth it")
    p3 = words(12.0, "thanks for watching bye")
    phrases = {"f1": [
        Phrase("f1.p1", "f1", p1[0].start, p1[-1].end, " ".join(w.text for w in p1), p1),
        Phrase("f1.p2", "f1", p2[0].start, p2[-1].end, " ".join(w.text for w in p2), p2),
        Phrase("f1.p3", "f1", p3[0].start, p3[-1].end, " ".join(w.text for w in p3), p3),
    ]}
    shots = ShotList(shots=[
        Shot(file_id="f2", index=1, start=0, end=8, sharpness=100, shake=1, brightness=0.5, clipped=0, rep_time=4),
        Shot(file_id="f2", index=2, start=8, end=20, sharpness=90, shake=1, brightness=0.5, clipped=0, rep_time=12),
    ])
    return Footage(ingest, phrases, shots)


def test_speech_cuts_fall_between_words_with_air(footage):
    file_id, a, b, said = speech_cut(footage, "f1.p1", start_at="so today", end_after="mountain")
    assert said == "so today we climb the mountain"
    assert a == 1320  # "so" starts at 1.4 s, minus 80 ms
    assert b == 3820  # "mountain" ends at 3.7 s, plus 120 ms
    with pytest.raises(PickError):
        speech_cut(footage, "f1.p1", start_at="hello there")
    with pytest.raises(PickError):
        speech_cut(footage, "f1.p9")


def test_padding_never_reaches_into_the_neighbouring_word(footage):
    _, a, _, _ = speech_cut(footage, "f1.p1", start_at="today", end_after="today")
    assert a == 1720  # "today" starts at 1.8 s; 80 ms earlier is still after "so" ends (1.7 s)
    _, _, b, _ = speech_cut(footage, "f1.p1", start_at="um", end_after="so")
    assert b == 1800  # 120 ms after "so" would reach into "today", which starts at 1.8 s


def test_draft_operations_keep_the_timeline_consistent(footage):
    plan = empty_plan("talking-head", EditFormat(target_seconds=5, max_seconds=6))
    d = Draft(plan, footage)
    d.add_clip(phrase_from="f1.p1", start_at="so")
    d.add_clip(phrase_from="f1.p2")
    d.add_clip(track="V2", shot="f2#1", start=1, end=3, over="#2", offset=0.5)
    d.add_mark(track="T1", text="The climb", over="#1", offset=0.2, duration=2)
    main = plan.track("V1").clips
    assert [c.label for c in main] == ["#1", "#2"] and main[1].at == main[0].dur
    broll = plan.track("V2").clips[0]
    assert broll.label == "#3" and broll.muted and broll.at == main[1].at + 500
    d.move_clip("#2", after="start")
    assert [c.label for c in plan.track("V1").clips] == ["#2", "#1"]
    assert plan.track("V2").clips[0].at == 500  # the b-roll moved with #2
    note = d.add_clip(phrase_from="f1.p3")
    assert "over the 6 s limit" in note
    d.trim_clip("#4", end_after="watching")
    assert plan.by_label("#4")[1].transcript == "thanks for watching"
    d.remove("#4")
    d.merge_tracks("V2", "V1")
    assert plan.track("V2") is None and [c.label for c in plan.track("V1").clips][0] == "#2"
    d.split_track("T1", ["T1"], "Titles")
    assert any(t.name == "Titles" for t in plan.tracks)
    with pytest.raises(PickError):
        d.remove("#99")
    with pytest.raises(PickError):
        d.add_clip(track="V1", shot="f2#1", over="#1")


def test_first_edit_is_built_from_claudes_plan_and_bad_ids_become_warnings(footage):
    profile = load_edit_profiles([])["talking-head"]
    data = {
        "strategy": "Climb story.",
        "main": [{"phrase_from": "f1.p1", "start_at": "so today", "role": "hook", "reason": "r"},
                 {"phrase_from": "f1.p7", "role": "x", "reason": "missing"},
                 {"phrase_from": "f1.p2", "role": "peak", "reason": "r"}],
        "broll": [{"over": 3, "offset": 1.0, "shot": "f2#2", "reason": "trees"}],
        "captions": [{"over": 1, "offset": 0, "duration": 2, "text": "Summit day"}],
        "music": "calm acoustic", "sound_effects": [],
    }
    first = build_first_edit(data, profile, footage, limit=30)
    plan = first.plan
    assert [c.role for c in plan.track("V1").clips] == ["hook", "peak"]
    assert len(first.warnings) == 1 and "f1.p7" in first.warnings[0]
    assert plan.track("V2").clips[0].anchor == plan.track("V1").clips[1].id
    assert plan.track("A2").marks[0].text == "calm acoustic" and plan.format.max_seconds == 30


def test_exports_and_versions(footage, tmp_path):
    d = Draft(empty_plan("generic", EditFormat()), footage)
    d.add_clip(phrase_from="f1.p2")
    store = DocumentStore(tmp_path, "edit", EditPlan)
    info = store.save(d.plan, author="analysis", summary="first")
    data = edit_json(store.load(), footage.ingest, info.version)
    assert data["sources"][0]["path"] == "talk.mp4"
    item = data["tracks"][2]["items"][0]
    assert item["label"] == "#1" and item["in"] == pytest.approx(5.92) and item["transcript"].startswith("it was")
    json.dumps(data)
    assert "| #1 |" in edit_markdown(store.load(), footage.ingest, info.version)
