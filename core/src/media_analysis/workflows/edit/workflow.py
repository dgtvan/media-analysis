"""The edit workflow: footage + a profile → a first edit plan (version 1 of the project's edit), plus the proxies and
filmstrips the app shows.

ingest → transcribe → shots → sound → describe → prepare → sequence
(understanding, shared)                      local      Claude

The project folder holds `.cache/` (stage results), `media/` (proxies, filmstrips) and `edit/` (edit versions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from media_analysis.engine.documents import DocumentStore, VersionInfo
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.events import EventSink, null_sink
from media_analysis.engine.settings import USER_HOME, Settings
from media_analysis.engine.workflow import CACHE_DIR_NAME, RunContext, RunOptions, Stage, Workflow, check_folders, check_output
from media_analysis.media import ffmpeg
from media_analysis.providers.llm import create_llm
from media_analysis.understand.describe import DescribeStage
from media_analysis.understand.ingest import IngestStage
from media_analysis.understand.models import Descriptions, Ingest
from media_analysis.understand.shots import ShotsStage
from media_analysis.understand.sound import SoundStage
from media_analysis.understand.transcribe import TranscribeStage
from media_analysis.understand.views import file_phrases
from media_analysis.workflows.edit.cuts import Footage
from media_analysis.workflows.edit.models import EditPlan
from media_analysis.workflows.edit.profiles.registry import EditProfile, load_edit_profiles
from media_analysis.workflows.edit.sequence import SequenceStage
from media_analysis.workflows.edit.export import write_exports
from media_analysis.workflows.edit.view import footage_view

USER_EDIT_PROFILES_DIR = USER_HOME / "edit-profiles"
PROJECT_FILE = "project.json"
MEDIA_DIR = "media"
EDIT_DIR_KIND = "edit"


def edit_store(project_dir: Path) -> DocumentStore[EditPlan]:
    return DocumentStore(project_dir, EDIT_DIR_KIND, EditPlan)


def proxy_path(project_dir: Path, fingerprint: str) -> Path:
    return project_dir / MEDIA_DIR / f"{fingerprint}.proxy.mp4"


def strip_path(project_dir: Path, fingerprint: str) -> Path:
    return project_dir / MEDIA_DIR / f"{fingerprint}.strip.jpg"


def strip_every(duration: float) -> float:
    """Seconds between filmstrip frames: one per second, fewer for long clips (images can't be wider than ~65,000 px)."""
    return max(1.0, math.ceil(duration / 300))


class PrepareStage(Stage):
    """Stage 'prepare': proxies for smooth playback and filmstrips for the timeline (no AI). They are named by file
    content, so a file imported again is not prepared again."""

    name = "prepare"

    def run(self, ingest: Ingest, project_dir: Path) -> None:
        rep = self.rep
        todo = [f for f in ingest.files if f.has_video and not (
            proxy_path(project_dir, f.fingerprint).exists() and strip_path(project_dir, f.fingerprint).exists())]
        if not todo:
            rep.skipped("ready")
            return
        rep.started("making proxies and filmstrips")
        for n, f in enumerate(todo):
            rep.progress(n, len(todo), f.name)
            if not proxy_path(project_dir, f.fingerprint).exists():
                ffmpeg.make_proxy(Path(f.path), proxy_path(project_dir, f.fingerprint))
            if not strip_path(project_dir, f.fingerprint).exists():
                ffmpeg.filmstrip(Path(f.path), strip_path(project_dir, f.fingerprint), f.duration, every=strip_every(f.duration))
        rep.done(f"{len(todo)} clip(s)")


class ProjectInfo(BaseModel):
    """What a project was made from, so it can be opened again (project.json)."""

    profile_id: str
    max_seconds: int | None = None
    files: list[str] | None = None
    input_dir: str | None = None
    name: str = ""


def save_project(project_dir: Path, info: ProjectInfo) -> None:
    path = project_dir / PROJECT_FILE
    if path.exists() and not info.name:
        info.name = load_project_info(project_dir).name
    project_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(info.model_dump_json(indent=2), encoding="utf-8")


def load_project_info(project_dir: Path) -> ProjectInfo:
    path = project_dir / PROJECT_FILE
    if not path.exists():
        raise MediaAnalysisError(f"{project_dir} is not an edit project (no {PROJECT_FILE}).")
    return ProjectInfo.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass
class EditOptions(RunOptions):
    profile_id: str = "generic"
    max_seconds: int | None = None
    """The project's limit for the edit's length; None = the profile's limit, if it has one."""
    files: list[Path] | None = None
    """Footage imported from anywhere; None = every video/audio file in `input_dir`."""


@dataclass
class EditResult:
    version: VersionInfo
    plan: EditPlan
    warnings: list[str] = field(default_factory=list)


@dataclass
class Understanding:
    """What the edit's chat needs again later; rebuilt from the cache when a project is opened."""

    ingest: Ingest
    footage: Footage
    descriptions: Descriptions | None
    view: str


def understand_footage(ctx: RunContext, opts: EditOptions, profile: EditProfile) -> Understanding:
    ingest = IngestStage(ctx).run(opts.input_dir, opts.files)
    ctx.timer.audio_seconds = sum(f.duration for f in ingest.files if f.uses_audio)
    transcript = TranscribeStage(ctx).run(ingest)
    shots = ShotsStage(ctx).run(ingest)
    sound = SoundStage(ctx).run(ingest)
    descriptions = DescribeStage(ctx).run(ingest, shots) if profile.analysis.describe else None
    phrases = file_phrases(ingest, transcript)
    view = footage_view(ingest, phrases, shots, descriptions, sound)
    return Understanding(ingest, Footage(ingest, phrases, shots), descriptions, view)


class EditWorkflow(Workflow[EditOptions, EditResult]):
    name = "edit"
    stages = [IngestStage, TranscribeStage, ShotsStage, SoundStage, DescribeStage, PrepareStage, SequenceStage]

    def __init__(self, settings: Settings, sink: EventSink = null_sink) -> None:
        super().__init__(settings, sink)
        self._profile: EditProfile | None = None
        self._understanding: Understanding | None = None

    def check(self, opts: EditOptions) -> None:
        if opts.files is None:
            check_folders(opts.input_dir, opts.output_dir)
            return
        missing = [str(p) for p in opts.files if not p.is_file()]
        if missing:
            raise MediaAnalysisError("Footage not found: " + ", ".join(missing))
        check_output(opts.output_dir, (CACHE_DIR_NAME, EDIT_DIR_KIND, "project.json"))

    def prepare(self, opts: EditOptions) -> None:
        profiles = load_edit_profiles([USER_EDIT_PROFILES_DIR])
        if opts.profile_id not in profiles:
            raise MediaAnalysisError(f"Unknown edit profile '{opts.profile_id}'. Available: {', '.join(sorted(profiles))}.")
        self._profile = profiles[opts.profile_id]
        # Cutting between words needs word times; keeping fillers lets them be cut.
        ts = self.settings.transcription
        ts.word_timestamps = ts.word_timestamps or self._profile.analysis.words
        ts.verbatim = ts.verbatim or self._profile.analysis.verbatim

    def create_llm(self) -> Any:
        llm = self.settings.llm
        return create_llm(llm.backend, llm.model, llm.effort, llm.claude_profile)

    def execute(self, ctx: RunContext, opts: EditOptions) -> EditResult:
        profile = self._profile
        assert profile is not None
        u = self._understanding = understand_footage(ctx, opts, profile)
        PrepareStage(ctx).run(u.ingest, opts.output_dir)
        first = SequenceStage(ctx).run(u.view, profile, u.footage, opts.max_seconds)
        store = edit_store(opts.output_dir)
        # A new suggestion (another profile or limit, new footage) becomes a new version; the same one again doesn't.
        last_suggestion = next((v for v in reversed(store.versions()) if v.author == "analysis"), None)
        if last_suggestion is None or store.load(last_suggestion.version) != first.plan:
            info = store.save(first.plan, author="analysis", summary=f"Suggestion ({profile.name})")
        else:
            info = next(v for v in store.versions() if v.version == store.current())
        save_project(opts.output_dir, ProjectInfo(
            profile_id=profile.id, max_seconds=opts.max_seconds,
            files=[f.path for f in u.ingest.files] if opts.files is not None else None,
            input_dir=str(opts.input_dir) if opts.files is None else None,
        ))
        write_exports(store.load(), u.ingest, info.version, opts.output_dir / "export")
        return EditResult(version=info, plan=store.load(), warnings=first.warnings)

    def record_fields(self, result: EditResult | None) -> tuple[dict[str, Any], dict[str, Any]]:
        files = self._understanding.ingest.files if self._understanding else []
        return {
            "media": {
                "files": [{"name": f.name, "fingerprint": f.fingerprint, "seconds": round(f.duration, 1),
                           "has_video": f.has_video, "has_audio": f.has_audio} for f in files],
                "video_seconds": round(sum(f.duration for f in files if f.has_video), 1),
                "audio_seconds": round(self.timer.audio_seconds, 1),
            },
            "edit": None if result is None else {
                "version": result.version.version, "clips": len(result.plan.track("V1").clips),
                "seconds": round(result.plan.duration() / 1000, 1),
            },
        }, {"profile": self._profile.id if self._profile else None}


def open_project(project_dir: Path, settings: Settings, sink: EventSink = null_sink) -> tuple[Understanding, EditProfile]:
    """The understanding of an analysed project, rebuilt from its cache (no new analysis), and its profile."""
    from media_analysis.engine.cache import Cache

    info = load_project_info(project_dir)
    wf = EditWorkflow(settings, sink)
    opts = EditOptions(
        input_dir=Path(info.input_dir) if info.input_dir else project_dir, output_dir=project_dir,
        profile_id=info.profile_id, max_seconds=info.max_seconds,
        files=[Path(p) for p in info.files] if info.files is not None else None,
    )
    wf.prepare(opts)
    ctx = RunContext(wf, Cache(project_dir / CACHE_DIR_NAME), None)
    return understand_footage(ctx, opts, wf._profile), wf._profile  # type: ignore[arg-type]
