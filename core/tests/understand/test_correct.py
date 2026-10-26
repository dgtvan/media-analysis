from media_analysis.engine.cache import Cache
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import RunContext
from media_analysis.understand.correct import CorrectStage, corrections_markdown
from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, TranscriptSegment, Word
from media_analysis.workflows.report.workflow import ReportWorkflow


class FixingLLM:
    model = None
    effort = None

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def generate_json(self, system, content, schema, on_activity=None):
        self.calls.append("\n".join(block.text for block in content))
        return self.answer


def meeting():
    ingest = Ingest(files=[
        MediaFile(id="f1", path="audio-mic.m4a", name="audio-mic.m4a", fingerprint="a", duration=60, has_video=False,
                  has_audio=True, speaker="Me"),
        MediaFile(id="f2", path="audio-system.m4a", name="audio-system.m4a", fingerprint="b", duration=60,
                  has_video=False, has_audio=True, speaker="Others"),
    ])
    transcript = Transcript(files=[
        FileTranscript(file_id="f1", language="vi", segments=[
            TranscriptSegment(start=10, end=12, text="chưa có tạo subcription", language="vi",
                              words=[Word(start=10, end=11, text="chưa")]),
        ]),
        FileTranscript(file_id="f2", language="vi", segments=[
            TranscriptSegment(start=2, end=4, text="ờ get layon rồi", language="vi"),
            TranscriptSegment(start=20, end=22, text="ok anh", language="vi"),
        ]),
    ])
    return ingest, transcript


def run(tmp_path, answer, terms=""):
    settings = Settings(stats_file=None)
    (tmp_path / "terms.txt").write_text(terms, encoding="utf-8")
    settings.transcription.terms_file = tmp_path / "terms.txt"
    ctx = RunContext(ReportWorkflow(settings), Cache(tmp_path / ".cache"), None)
    llm = ctx._llm = FixingLLM(answer)
    ingest, transcript = meeting()
    return CorrectStage(ctx).run(ingest, transcript), llm, ingest


def test_corrects_only_the_lines_claude_changed_and_keeps_the_rest(tmp_path):
    # Lines are numbered in time order across files: [0] Others 2 s, [1] Me 10 s, [2] Others 20 s.
    answer = {"corrections": [{"id": 0, "text": "ờ get client rồi"}, {"id": 1, "text": "chưa có tạo subscription"},
                              {"id": 2, "text": "ok anh"}, {"id": 9, "text": "made up"}]}
    result, llm, ingest = run(tmp_path, answer, terms="client\nsubscription\n")
    sent = llm.calls[0]
    assert "[0] 00:00:02 Others: ờ get layon rồi" in sent and "[1] 00:00:10 Me: chưa có tạo subcription" in sent
    assert "- client" in sent and "Vietnamese 100%" in sent
    me, others = result.transcript.files
    assert me.segments[0].text == "chưa có tạo subscription" and me.segments[0].words is None
    assert [s.text for s in others.segments] == ["ờ get client rồi", "ok anh"]
    assert [(c.before, c.after) for c in result.corrections] == [
        ("ờ get layon rồi", "ờ get client rồi"), ("chưa có tạo subcription", "chưa có tạo subscription")]
    assert "before: ờ get layon rồi" in corrections_markdown(ingest, result.corrections)


def test_corrections_are_cached(tmp_path):
    answer = {"corrections": [{"id": 1, "text": "chưa có tạo subscription"}]}
    run(tmp_path, answer)
    again, llm, _ = run(tmp_path, {"corrections": []})
    assert llm.calls == [] and len(again.corrections) == 1
