"""Runs a workflow: an ordered list of stages over one input folder.

Every stage stores its result in the cache under a key derived from its inputs and settings, so a rerun only repeats
the stages whose inputs changed (or those from `force_from` onwards). Every run ends with a performance summary and
one line in the run statistics, also when it fails.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

from media_analysis.engine.cache import Cache, M, digest
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.events import EventSink, Reporter, null_sink
from media_analysis.engine.perf import StageTimer, TimedLLM, append_run_record, run_record
from media_analysis.engine.settings import Settings

CACHE_DIR_NAME = ".cache"


@dataclass
class RunOptions:
    input_dir: Path
    output_dir: Path
    force_from: str | None = None
    clean_cache: bool = False
    fresh: bool = False
    """Delete the cache of earlier runs first, so every stage runs again."""
    project_name: str | None = None
    """The name the user gave the project, when it differs from its folder name (run statistics only)."""
    log_file: Path | None = None
    """Where the caller logs this run (run statistics only)."""


class RecordingCache:
    """The run's cache as stages use it: next to every result it saves how it was made (what the stage noted,
    the Claude models and settings), and it tells the timer how every result taken from the cache was made."""

    def __init__(self, cache: Cache, timer: StageTimer, settings: Settings) -> None:
        self._cache = cache
        self._timer = timer
        self._settings = settings
        self.root = cache.root

    def dir(self, *parts: str) -> Path:
        return self._cache.dir(*parts)

    def path(self, stage: str, key: str, suffix: str = ".json") -> Path:
        return self._cache.path(stage, key, suffix)

    def load(self, stage: str, key: str, model: type[M]) -> M | None:
        value = self._cache.load(stage, key, model)
        if value is not None:
            self._timer.reused(stage, self._cache.load_made_with(stage, key))
        return value

    def save(self, stage: str, key: str, value: BaseModel) -> Path:
        path = self._cache.save(stage, key, value)
        made_with = self._timer.made_with(stage)
        if made_with.get("claude_models"):
            llm = self._settings.llm
            made_with.update(claude_backend=llm.backend, claude_effort=llm.effort)
        self._cache.save_made_with(stage, key, made_with)
        return path


class RunContext:
    """What the stages of one run share: settings, cache, events and the Claude client."""

    def __init__(self, workflow: Workflow, cache: Cache, force_from: str | None, cache_only: bool = False) -> None:
        self.settings = workflow.settings
        self.cache_only = cache_only
        """Only read results from the cache; a stage that would have to do work fails instead."""
        self.timer = workflow.timer
        self.sink: EventSink = workflow.timer
        self.cache = RecordingCache(cache, workflow.timer, workflow.settings)
        self._workflow = workflow
        self._force_from = force_from
        self._llm: Any = None

    @property
    def llm(self) -> Any:
        """The Claude client, created on first use; every call is timed and counted towards the current stage."""
        if self._llm is None:
            self._llm = TimedLLM(self._workflow.create_llm(), self.timer)
        return self._llm

    def forced(self, stage: str) -> bool:
        names = self._workflow.stage_names()
        return self._force_from is not None and names.index(stage) >= names.index(self._force_from)


class Stage:
    """One step of a workflow. Subclasses set `name` (and `version`) and implement `run` with their own inputs."""

    name: ClassVar[str]
    version: ClassVar[int] = 1
    """Bump when the stage's algorithm changes in a way that invalidates cached results."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.settings = ctx.settings
        self.cache = ctx.cache
        self.force = ctx.forced(self.name)
        self.rep = Reporter(ctx.sink, self.name)

    def key(self, *parts: Any) -> str:
        """Cache key of a result: everything it depends on, plus the stage version."""
        return digest([*parts, self.version])

    def note(self, **made_with: Any) -> None:
        """Records what does this stage's work, e.g. note(transcriber="..."); kept with the results it caches."""
        self.ctx.timer.note(self.name, **made_with)

    def need_work(self) -> None:
        """Called before a stage does real work; refuses when the run may only read the cache."""
        if self.ctx.cache_only:
            raise MediaAnalysisError(f"'{self.name}' isn't in the cache; analyse the files again.")


O = TypeVar("O", bound=RunOptions)
R = TypeVar("R")


class Workflow(Generic[O, R]):
    """Turns one input folder into one output folder. Subclasses list their stages and implement `execute`."""

    name: ClassVar[str]
    stages: ClassVar[list[type[Stage]]]

    def __init__(self, settings: Settings, sink: EventSink = null_sink) -> None:
        self.settings = settings
        self.timer = StageTimer(sink)
        self.started = datetime.now(timezone.utc)

    @classmethod
    def stage_names(cls) -> list[str]:
        return [s.name for s in cls.stages]

    def run(self, opts: O) -> R:
        """Runs every stage; always ends with a summary event of how long each stage took, also on failure, and
        appends the run to the stats file."""
        started = self.started = datetime.now(timezone.utc)
        failed, error, result = True, None, None
        try:
            result = self._run(opts)
            failed = False
            return result
        except BaseException as e:
            error = str(e) or type(e).__name__
            raise
        finally:
            summary = self.timer.summary(failed)
            self.timer(summary)
            self._record(opts, started, summary, result, error)

    def _run(self, opts: O) -> R:
        opts = replace(opts, input_dir=opts.input_dir.resolve(), output_dir=opts.output_dir.resolve())
        self.check(opts)
        self.prepare(opts)
        opts.output_dir.mkdir(parents=True, exist_ok=True)
        if opts.fresh:
            shutil.rmtree(opts.output_dir / CACHE_DIR_NAME, ignore_errors=True)
        cache = Cache(opts.output_dir / CACHE_DIR_NAME)
        result = self.execute(RunContext(self, cache, opts.force_from), opts)
        if opts.clean_cache:
            shutil.rmtree(cache.root, ignore_errors=True)
        return result

    def check(self, opts: O) -> None:
        """Checks the input and output folders."""
        check_folders(opts.input_dir, opts.output_dir)

    def prepare(self, opts: O) -> None:
        """Checks the options before anything is written."""

    def execute(self, ctx: RunContext, opts: O) -> R:
        raise NotImplementedError

    def create_llm(self) -> Any:
        raise NotImplementedError(f"The {self.name} workflow doesn't use Claude.")

    def record_fields(self, result: R | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """What this workflow adds to the run statistics: (top-level fields, settings fields)."""
        return {}, {}

    def _record(self, opts: O, started: datetime, summary, result: R | None, error: str | None) -> None:
        path = self.settings.stats_file
        if path is None:
            return
        try:
            fields, settings_fields = self.record_fields(result)
            record = run_record(
                workflow=self.name, started=started, input_dir=opts.input_dir.resolve(),
                output_dir=opts.output_dir.resolve(), summary=summary, settings=self.settings, error=error,
                fields=fields, settings_fields=settings_fields, project_name=opts.project_name,
                log_file=opts.log_file, fresh=opts.fresh, force_from=opts.force_from,
            )
            append_run_record(path, record)
        except OSError as e:
            Reporter(self.timer, "summary").warning(f"Could not record the run statistics in {path}: {e}")


def check_folders(input_dir: Path, output_dir: Path) -> None:
    if not input_dir.is_dir():
        raise MediaAnalysisError(f"Input folder not found: {input_dir}")
    if output_dir == input_dir:
        raise MediaAnalysisError("Output folder must be different from the input folder.")
    check_output(output_dir)


def check_output(output_dir: Path, own_entries: tuple[str, ...] = (CACHE_DIR_NAME,)) -> None:
    """The output folder must be new, empty, or one this program made (it has one of `own_entries`)."""
    if output_dir.exists():
        if not output_dir.is_dir():
            raise MediaAnalysisError(f"Output path is not a folder: {output_dir}")
        entries = [p.name for p in output_dir.iterdir()]
        if entries and not any(e in entries for e in own_entries):
            raise MediaAnalysisError(
                f"Output folder {output_dir} is not empty and was not created by media-analysis. "
                "Use an empty or new folder."
            )
