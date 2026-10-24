from media_analysis.engine.cache import Cache
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import RunContext
from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, VisualScan
from media_analysis.workflows.report.classify import ClassifyStage
from media_analysis.workflows.report.templates.registry import load_templates
from media_analysis.workflows.report.workflow import ReportWorkflow


class FakeLLM:
    model = None
    effort = None

    def __init__(self, answer):
        self.answer = answer

    def generate_json(self, system, content, schema, on_activity=None):
        return self.answer


def classify_with(answer, tmp_path):
    ctx = RunContext(ReportWorkflow(Settings()), Cache(tmp_path), None)
    ctx._llm = FakeLLM(answer)
    ingest = Ingest(files=[MediaFile(id="f1", path="talk.mp3", name="talk.mp3", fingerprint="f1", duration=60,
                                     has_video=False, has_audio=True)])
    transcript = Transcript(files=[FileTranscript(file_id="f1", language="en", segments=[])])
    return ClassifyStage(ctx).run(ingest, transcript, VisualScan(segments=[]), load_templates([]), None)


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
