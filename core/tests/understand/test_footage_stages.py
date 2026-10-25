import subprocess
from pathlib import Path

import pytest

from media_analysis.engine.cache import Cache
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.settings import Settings
from media_analysis.engine.workflow import RunContext
from media_analysis.media import ffmpeg
from media_analysis.understand.describe import DescribeStage
from media_analysis.understand.ingest import IngestStage
from media_analysis.understand.shots import ShotsStage
from media_analysis.understand.sound import SoundStage
from media_analysis.understand.views import shot_view
from media_analysis.workflows.report.workflow import ReportWorkflow


@pytest.fixture(scope="module")
def footage(tmp_path_factory):
    try:
        exe = ffmpeg.find_binary("ffmpeg")
    except MediaAnalysisError:
        pytest.skip("ffmpeg not found")
    folder = tmp_path_factory.mktemp("footage")
    subprocess.run([
        exe, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
        "-f", "lavfi", "-i", "mandelbrot=size=320x180:rate=25,trim=duration=3",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=6,volume='if(between(t,2,3),0,1)':eval=frame",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(folder / "clip.mp4"),
    ], check=True)
    return folder


class DescribingLLM:
    model = None
    effort = None
    calls = 0

    def generate_json(self, system, content, schema, on_activity=None):
        DescribingLLM.calls += 1
        ids = [line.split(":")[0][2:] for line in content[0].text.splitlines() if line.startswith("- ")]
        # Leaves the last shot out, to check that it still gets an entry.
        return {"shots": [{"id": i, "caption": f"shot {i}", "subjects": [], "place": "", "action": "",
                           "tags": ["Test"], "issues": []} for i in ids[:-1]]}


def test_shots_sound_and_describe_on_a_real_clip(footage, tmp_path):
    ctx = RunContext(ReportWorkflow(Settings(stats_file=None)), Cache(tmp_path), None)
    ctx._llm = DescribingLLM()
    ingest = IngestStage(ctx).run(Path(footage))
    shots = ShotsStage(ctx).run(ingest)
    assert [s.index for s in shots.shots] == [1, 2]
    sound = SoundStage(ctx).run(ingest)
    assert sound.files[0].loudness is not None and any(1.8 < a < 2.2 for a, _ in sound.files[0].silences)
    described = DescribeStage(ctx).run(ingest, shots)
    assert [d.caption for d in described.shots] == ["shot f1#1", "(not described)"]
    assert described.shots[0].tags == ["test"]
    view = shot_view(ingest, shots, described)
    assert "f1#1 [00:00:00-00:00:03] shot f1#1 (tags: test)" in view
    calls = DescribingLLM.calls
    DescribeStage(ctx).run(ingest, shots)
    assert DescribingLLM.calls == calls
