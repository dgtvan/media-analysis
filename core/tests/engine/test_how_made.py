from datetime import datetime, timezone

from pydantic import BaseModel

from media_analysis.engine.cache import Cache
from media_analysis.engine.events import Event, EventKind
from media_analysis.engine.how_made import how_made
from media_analysis.engine.perf import StageTimer
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import RecordingCache


class Result(BaseModel):
    text: str


def run_stage(timer: StageTimer, stage: str, body) -> None:
    timer(Event(EventKind.STAGE_STARTED, stage, ""))
    body()
    timer(Event(EventKind.STAGE_DONE, stage, ""))


def test_results_keep_how_they_were_made_and_report_it_when_reused(tmp_path):
    settings = Settings(stats_file=None, resource_use="Light (25% of the CPU, idle priority)")
    cache = Cache(tmp_path / ".cache")

    first = StageTimer(lambda e: None)
    rc = RecordingCache(cache, first, settings)

    def transcribe():
        first.note("transcribe", transcriber="Azure AI Speech at x (en-US, vi-VN)")
        rc.save("transcribe", "k1", Result(text="hi"))

    def report():
        first.add_claude_call(2.0, None, "claude-opus-5-5")
        rc.save("report", "k2", Result(text="# Report"))

    run_stage(first, "transcribe", transcribe)
    run_stage(first, "report", report)
    assert cache.load_made_with("report", "k2")["claude_models"] == ["claude-opus-5-5"]
    assert cache.load_made_with("report", "k2")["claude_backend"] == "subscription"

    second = StageTimer(lambda e: None)
    rc2 = RecordingCache(cache, second, settings)
    second(Event(EventKind.STAGE_STARTED, "transcribe", ""))
    assert rc2.load("transcribe", "k1", Result) == Result(text="hi")
    second(Event(EventKind.STAGE_SKIPPED, "transcribe", "cached"))
    run_stage(second, "report", lambda: second.add_claude_call(1.0, None, "claude-sonnet-5-5"))

    doc = how_made("How this report was made", second, settings, datetime.now(timezone.utc), [("Template", "Meeting")])
    assert "**Resource use:** Light (25% of the CPU, idle priority)" in doc
    assert "**Template:** Meeting" in doc
    assert "| Transcribe speech | reused | earlier (" in doc and "Azure AI Speech at x (en-US, vi-VN)" in doc
    assert "| Write the report | ran, 0 s, 1 Claude call | Claude claude-sonnet-5-5 |" in doc


def test_results_cached_before_this_was_kept_say_so(tmp_path):
    cache = Cache(tmp_path / ".cache")
    cache.save("scan", "k", Result(text="x"))
    timer = StageTimer(lambda e: None)
    timer(Event(EventKind.STAGE_STARTED, "scan", ""))
    RecordingCache(cache, timer, Settings(stats_file=None)).load("scan", "k", Result)
    timer(Event(EventKind.STAGE_SKIPPED, "scan", "cached"))
    doc = how_made("How", timer, Settings(stats_file=None), datetime.now(timezone.utc), [])
    assert "| Scan the video | reused | earlier (details not kept) |" in doc
