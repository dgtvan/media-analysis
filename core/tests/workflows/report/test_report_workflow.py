import json

import pytest

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import CACHE_DIR_NAME
from media_analysis.workflows.report.workflow import ReportOptions, ReportWorkflow


def test_stages_run_in_the_documented_order():
    assert ReportWorkflow.stage_names() == [
        "ingest", "transcribe", "scan", "classify", "plan", "extract", "read", "report", "assemble",
    ]


def test_every_run_appends_one_json_line_for_statistics(tmp_path):
    stats = tmp_path / "stats" / "runs.jsonl"
    settings = Settings(stats_file=stats)
    for _ in range(2):
        with pytest.raises(MediaAnalysisError):
            ReportWorkflow(settings).run(ReportOptions(
                input_dir=tmp_path / "missing", output_dir=tmp_path / "out", project_name="Weekly call",
                log_file=tmp_path / "run.log", fresh=True,
            ))

    records = [json.loads(line) for line in stats.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 and records[0]["run_id"] != records[1]["run_id"]
    r = records[0]
    assert r["schema"] == 1 and r["workflow"] == "report" and r["outcome"] == "failed" and r["error"]
    assert r["project"]["name"] == "missing" and r["project"]["display_name"] == "Weekly call"
    assert r["fresh"] is True and r["force_from"] is None
    assert r["log_file"] == str(tmp_path / "run.log") and r["media"]["files"] == [] and r["template"] is None
    assert r["claude"]["calls"] == 0 and isinstance(r["stages"], list) and r["settings"]["backend"] == "subscription"
    assert r["settings"]["max_frames"] == 30


def test_fresh_run_clears_the_cache_of_earlier_runs(tmp_path):
    (tmp_path / "in").mkdir()
    marker = tmp_path / "out" / CACHE_DIR_NAME / "transcript.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    settings = Settings(stats_file=None)

    with pytest.raises(MediaAnalysisError):  # no media in the input folder
        ReportWorkflow(settings).run(ReportOptions(input_dir=tmp_path / "in", output_dir=tmp_path / "out"))
    assert marker.exists()
    with pytest.raises(MediaAnalysisError):
        ReportWorkflow(settings).run(ReportOptions(input_dir=tmp_path / "in", output_dir=tmp_path / "out", fresh=True))
    assert not marker.exists()
