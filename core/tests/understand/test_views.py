from media_analysis.engine.cache import Cache, digest
from media_analysis.engine.settings import Settings, TranscriptionSettings
from media_analysis.engine.workflow import RunContext
from media_analysis.understand.condense import CondenseStage, split_lines
from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, TranscriptSegment
from media_analysis.understand.views import PhraseRules, transcript_view
from media_analysis.workflows.report.workflow import ReportWorkflow


def talk(*segments):
    ingest = Ingest(files=[MediaFile(id="f1", path="talk.mp3", name="talk.mp3", fingerprint="f1", duration=600,
                                     has_video=False, has_audio=True)])
    transcript = Transcript(files=[FileTranscript(file_id="f1", language="en", segments=[
        TranscriptSegment(start=a, end=b, text=t) for a, b, t in segments
    ])])
    return ingest, transcript


def lines(text):
    return [line for line in text.splitlines() if line.startswith(("[", "("))]


def test_close_segments_join_into_one_phrase_with_start_and_end():
    ingest, transcript = talk((1, 4, "So the plan is"), (4.5, 7, "to ship on Friday."), (12, 14, "Any questions?"))
    assert lines(transcript_view(ingest, transcript)) == [
        "[00:00:01-00:00:07] So the plan is to ship on Friday.",
        "[00:00:12-00:00:14] Any questions?",
    ]


def test_phrases_stop_growing_at_the_length_limit():
    ingest, transcript = talk(*[(i * 5, i * 5 + 4.8, f"part {i}.") for i in range(8)])
    phrases = lines(transcript_view(ingest, transcript, PhraseRules(max_phrase=20)))
    assert len(phrases) == 2 and phrases[0].startswith("[00:00:00-00:00:19]")


def test_long_silences_are_marked():
    ingest, transcript = talk((0, 3, "Let me share my screen."), (95, 98, "Okay, back."))
    assert lines(transcript_view(ingest, transcript)) == [
        "[00:00:00-00:00:03] Let me share my screen.",
        "(no speech for 1m 32s)",
        "[00:01:35-00:01:38] Okay, back.",
    ]


def test_default_transcription_key_is_unchanged_by_the_new_options():
    ts = TranscriptionSettings(models_dir=None)
    before = ts.model_dump(exclude={"engine", "azure_endpoint", "terms_file", "device", "word_timestamps", "verbatim"})
    assert digest(ts.cache_key()) == digest(before)
    assert ts.model_copy(update={"word_timestamps": True}).cache_key()["word_timestamps"] is True


def test_split_lines_keeps_lines_whole():
    parts = split_lines("aaaa\nbbbb\ncccc\ndddd", 10)
    assert parts == ["aaaa\nbbbb", "cccc\ndddd"]


class NotesLLM:
    model = None
    effort = None

    def __init__(self):
        self.calls = 0

    def generate_text(self, system, content, on_activity=None):
        self.calls += 1
        return f"- [00:00:0{self.calls}] note {self.calls}"


def condense(view, tmp_path, budget):
    settings = Settings(stats_file=None)
    settings.llm.transcript_budget_chars = budget
    ctx = RunContext(ReportWorkflow(settings), Cache(tmp_path), None)
    llm = ctx._llm = NotesLLM()
    return CondenseStage(ctx).run(view), llm


def test_short_views_pass_through_without_claude(tmp_path):
    result, llm = condense("[00:00:01-00:00:02] Hi.", tmp_path, budget=1000)
    assert not result.condensed and result.text == "[00:00:01-00:00:02] Hi." and llm.calls == 0


def test_long_views_are_condensed_part_by_part_and_cached(tmp_path):
    view = "\n".join(f"[00:{i // 60:02d}:{i % 60:02d}-00:00:00] line {i} " + "x" * 200 for i in range(300))
    result, llm = condense(view, tmp_path, budget=30_000)
    assert result.condensed and result.parts == llm.calls >= 3
    assert "Notes on part 1 of" in result.text and "note 1" in result.text
    again, llm2 = condense(view, tmp_path, budget=30_000)
    assert again == result and llm2.calls == 0


def test_known_whisper_inventions_are_dropped():
    ingest, transcript = talk((1, 3, "Let's start."), (4, 6, "Hãy subscribe cho kênh La La School"), (6, 8, "The subscription is active."))
    assert lines(transcript_view(ingest, transcript)) == [
        "[00:00:01-00:00:03] Let's start.", "[00:00:06-00:00:08] The subscription is active.",
    ]
