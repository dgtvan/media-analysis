from media_analysis.core.assemble import assemble_output
from media_analysis.core.cache import Cache
from media_analysis.core.models import (
    Classification, Evidence, FileTranscript, Ingest, MediaFile, ReportDraft, SuggestedTemplate, Transcript,
    TranscriptSegment, VisualScan,
)
from media_analysis.core.perf import StageTimer
from media_analysis.providers.llm import Usage
from media_analysis.core.pipeline import Pipeline, _label_speakers
from media_analysis.config import Settings
from media_analysis.events import Event, EventKind
from media_analysis.templates.registry import load_templates, parse_template


def media(fid, name, video=False, audio=True, duration=60.0):
    return MediaFile(id=fid, path=name, name=name, fingerprint=fid, duration=duration, has_video=video, has_audio=audio)


def meeting_recorder_folder():
    return [media("f1", "audio-mic.wav"), media("f2", "audio-system.wav"), media("f3", "video.mp4", video=True, audio=False)]


def test_meeting_recorder_files_get_speakers():
    files = _label_speakers(meeting_recorder_folder())
    assert [f.speaker for f in files] == ["Me", "Others", None]
    plain = _label_speakers([media("f1", "talk.mp3"), media("f2", "part2.mp3")])
    assert all(f.speaker is None for f in plain)


def test_recorded_together_transcripts_merge_into_one_conversation():
    ingest = Ingest(files=_label_speakers(meeting_recorder_folder()))
    transcript = Transcript(files=[
        FileTranscript(file_id="f1", language="en", segments=[TranscriptSegment(start=9, end=11, text="Sure, here it is.")]),
        FileTranscript(file_id="f2", language="en", segments=[
            TranscriptSegment(start=5, end=8, text="Can you share the diagram?"),
            TranscriptSegment(start=12, end=14, text="Thanks."),
        ]),
        FileTranscript(file_id="f3", language=None, segments=[]),
    ])
    text = transcript.to_prompt_text(ingest)
    conversation = [line for line in text.splitlines() if line.startswith("[")]
    assert conversation == [
        "[00:00:05] Others: Can you share the diagram?",
        "[00:00:09] Me: Sure, here it is.",
        "[00:00:12] Others: Thanks.",
    ]
    assert "## f3: video.mp4" in text and "(no audio)" in text


class FakeLLM:
    model = None
    effort = None

    def __init__(self, answer):
        self.answer = answer

    def generate_json(self, system, content, schema, on_activity=None):
        return self.answer


def classify_with(answer, tmp_path):
    pipeline = Pipeline(Settings())
    pipeline._llm = FakeLLM(answer)
    ingest = Ingest(files=[media("f1", "talk.mp3")])
    transcript = Transcript(files=[FileTranscript(file_id="f1", language="en", segments=[])])
    return pipeline.classify(ingest, transcript, VisualScan(segments=[]), load_templates([]), None, Cache(tmp_path), False)


def test_no_fitting_template_falls_back_to_generic(tmp_path):
    result = classify_with({
        "template_id": "lecture-tutorial", "confidence": 0.2, "reason": "It is a cooking show.",
        "closest": ["interview"], "mismatch": ["No teaching structure"],
        "suggested_template": {"id": "cooking-show", "name": "Cooking show", "description": "A recipe demo.",
                               "signals": ["ingredients"], "evidence": ["the dish"], "sections": ["Recipe"]},
    }, tmp_path)
    assert result.fallback and result.template_id == "generic"
    assert result.closest == ["lecture-tutorial", "interview"]
    assert result.suggested.id == "cooking-show"


def test_fitting_template_is_used(tmp_path):
    result = classify_with({"template_id": "meeting", "confidence": 0.9, "reason": "r", "closest": [], "mismatch": []}, tmp_path)
    assert result.template_id == "meeting" and not result.fallback


def test_generic_template_is_never_offered_to_the_classifier():
    templates = load_templates([])
    assert templates["generic"].fallback
    assert [t.id for t in templates.values() if t.fallback] == ["generic"]


def test_generic_report_explains_the_fit_and_writes_a_usable_template(tmp_path):
    ingest = Ingest(files=[media("f1", "talk.mp3")])
    classification = Classification(
        template_id="generic", confidence=0.2, reason="It is a cooking show.", closest=["lecture-tutorial"],
        mismatch=["No teaching structure"], fallback=True,
        suggested=SuggestedTemplate(id="cooking-show", name="Cooking show", description="A recipe demo.",
                                    signals=["ingredients"], evidence=["the dish"], sections=["Recipe", "Steps"]),
    )
    out = tmp_path / "out"
    out.mkdir()
    report, _ = assemble_output(out, ReportDraft(title="T", markdown="# T\n\n## Summary\nx"), Evidence(items=[]),
                                ingest, load_templates([])["generic"], classification)
    text = report.read_text(encoding="utf-8")
    assert "## Template fit" in text and "No teaching structure" in text and "`lecture-tutorial`" in text
    suggested = parse_template((out / "reference/suggested-template.md").read_text(encoding="utf-8"), "suggested")
    assert suggested.id == "cooking-show" and suggested.sections == ["Recipe", "Steps"]


def test_summary_lists_each_stage_with_its_result():
    events = []
    timer = StageTimer(events.append)
    timer(Event(EventKind.STAGE_STARTED, "transcribe"))
    timer(Event(EventKind.STAGE_DONE, "transcribe", "12 segments"))
    timer(Event(EventKind.STAGE_SKIPPED, "scan", "cached"))
    timer(Event(EventKind.STAGE_STARTED, "report"))
    timer.add_claude_call(3.0)
    summary = timer.summary(failed=True)
    stages = {s["stage"]: s["result"] for s in summary.data["stages"]}
    assert stages == {"transcribe": "ran", "scan": "cached", "report": "failed"}
    assert summary.data["failed"] and summary.data["claude_calls"] == 1
    assert "Performance" in summary.message and "report" in summary.message
    assert len(events) == 5 and events[-1].kind == EventKind.INFO and "tokens not reported" in events[-1].message


def test_claude_tokens_are_logged_per_call_and_summed_per_stage():
    events = []
    timer = StageTimer(events.append)
    timer(Event(EventKind.STAGE_STARTED, "classify"))
    timer.add_claude_call(2.0, Usage.from_api({"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 50}, 0.01))
    timer(Event(EventKind.STAGE_DONE, "classify"))
    timer(Event(EventKind.STAGE_STARTED, "report"))
    timer.add_claude_call(5.0, Usage.from_api({"input_tokens": 2000, "cache_creation_input_tokens": 500, "output_tokens": 3000}, 0.2))
    timer.add_claude_call(4.0, Usage.from_api({"input_tokens": 1000, "output_tokens": 1000}, 0.1))
    timer(Event(EventKind.STAGE_DONE, "report"))

    calls = [e for e in events if e.kind == EventKind.INFO]
    assert calls[0].stage == "classify"
    assert calls[0].message == "Claude call 1: 00:00:02, 1,000 tokens in (900 from cache), 50 out, ~$0.01 at API rates"
    assert calls[1].data["claude_call"]["cache_write_tokens"] == 500

    summary = timer.summary(failed=False)
    by_stage = {s["stage"]: s for s in summary.data["stages"]}
    assert by_stage["report"]["claude_calls"] == 2
    assert by_stage["report"]["claude_usage"]["output_tokens"] == 4000
    assert summary.data["claude_usage"]["input_tokens"] == 3100
    assert abs(summary.data["claude_usage"]["cost_usd"] - 0.31) < 1e-9
    assert "Claude: 3 call(s)" in summary.message
    assert "report      2 calls" in summary.message and "4,500 tokens in" in summary.message


def test_every_run_appends_one_json_line_for_statistics(tmp_path):
    import json

    import pytest

    from media_analysis.core.pipeline import RunOptions
    from media_analysis.errors import MediaAnalysisError

    stats = tmp_path / "stats" / "runs.jsonl"
    settings = Settings(stats_file=stats)
    for _ in range(2):
        with pytest.raises(MediaAnalysisError):
            Pipeline(settings).run(RunOptions(input_dir=tmp_path / "missing", output_dir=tmp_path / "out",
                                              project_name="Weekly call", log_file=tmp_path / "run.log", fresh=True))

    records = [json.loads(line) for line in stats.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 and records[0]["run_id"] != records[1]["run_id"]
    r = records[0]
    assert r["schema"] == 1 and r["outcome"] == "failed" and r["error"]
    assert r["project"]["name"] == "missing" and r["project"]["display_name"] == "Weekly call"
    assert r["fresh"] is True and r["force_from"] is None
    assert r["log_file"] == str(tmp_path / "run.log") and r["media"]["files"] == [] and r["template"] is None
    assert r["claude"]["calls"] == 0 and isinstance(r["stages"], list) and r["settings"]["backend"] == "subscription"


def test_fresh_run_clears_the_cache_of_earlier_runs(tmp_path):
    import pytest

    from media_analysis.core.pipeline import CACHE_DIR_NAME, RunOptions
    from media_analysis.errors import MediaAnalysisError

    (tmp_path / "in").mkdir()
    marker = tmp_path / "out" / CACHE_DIR_NAME / "transcript.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    settings = Settings(stats_file=None)

    with pytest.raises(MediaAnalysisError):  # no media in the input folder
        Pipeline(settings).run(RunOptions(input_dir=tmp_path / "in", output_dir=tmp_path / "out"))
    assert marker.exists()
    with pytest.raises(MediaAnalysisError):
        Pipeline(settings).run(RunOptions(input_dir=tmp_path / "in", output_dir=tmp_path / "out", fresh=True))
    assert not marker.exists()
