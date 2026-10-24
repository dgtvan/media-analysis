from media_analysis.engine.events import Event, EventKind
from media_analysis.engine.perf import StageTimer
from media_analysis.engine.usage import Usage


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
