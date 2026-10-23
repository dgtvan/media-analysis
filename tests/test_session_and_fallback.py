from media_analysis.core.assemble import assemble_output
from media_analysis.core.cache import Cache
from media_analysis.core.models import (
    Classification, Evidence, FileTranscript, Ingest, MediaFile, ReportDraft, SuggestedTemplate, Transcript,
    TranscriptSegment, VisualScan,
)
from media_analysis.core.perf import StageTimer
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
    assert len(events) == 4
