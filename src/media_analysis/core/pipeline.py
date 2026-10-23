"""The processing pipeline: ingest → transcribe → scan → classify → plan → extract → read → report → assemble.

Every stage stores its artifact in the cache under a key derived from its inputs and settings, so a rerun only
repeats the stages whose inputs changed (or those from `force_from` onwards).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from media_analysis.config import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, Settings
from media_analysis.core import prompts
from media_analysis.core.assemble import assemble_output
from media_analysis.core.cache import Cache, digest, file_fingerprint
from media_analysis.core.perf import StageTimer
from media_analysis.core.models import (
    Candidates,
    Classification,
    Evidence,
    EvidenceItem,
    EvidenceRequest,
    FileTranscript,
    FrameCandidate,
    FramePlan,
    Ingest,
    MediaFile,
    ReportDraft,
    SuggestedTemplate,
    Transcript,
    VisualScan,
    VisualSegment,
    format_timecode,
)
from media_analysis.errors import MediaAnalysisError
from media_analysis.events import EventSink, Reporter, null_sink
from media_analysis.hardware import detect
from media_analysis.media import ffmpeg, vision
from media_analysis.providers.llm import LLM, Image, Text, create_llm
from media_analysis.providers.transcriber import SAMPLE_RATE, FasterWhisperTranscriber, split_points
from media_analysis.templates.registry import Template, load_templates

STAGES = ["ingest", "transcribe", "scan", "classify", "plan", "extract", "read", "report", "assemble"]
CACHE_DIR_NAME = ".cache"
# Bump when a stage's algorithm changes in a way that invalidates cached results.
STAGE_VERSIONS = {"transcribe": 1, "scan": 2, "classify": 2, "plan": 1, "extract": 1, "read": 1, "report": 1}


@dataclass
class RunOptions:
    input_dir: Path
    output_dir: Path
    template_id: str | None = None
    force_from: str | None = None
    clean_cache: bool = False


@dataclass
class RunResult:
    report_path: Path
    classification: Classification
    evidence_count: int
    warnings: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, settings: Settings, sink: EventSink = null_sink) -> None:
        self.settings = settings
        self.timer = StageTimer(sink)
        self.sink = self.timer
        self._llm: LLM | None = None

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            inner = create_llm(self.settings.llm.backend, self.settings.llm.model, self.settings.llm.effort)
            self._llm = _TimedLLM(inner, self.timer)
        return self._llm

    def _forced(self, stage: str, force_from: str | None) -> bool:
        return force_from is not None and STAGES.index(stage) >= STAGES.index(force_from)

    def run(self, opts: RunOptions) -> RunResult:
        """Runs every stage; always ends with a summary event of how long each stage took, also on failure."""
        failed = True
        try:
            result = self._run(opts)
            failed = False
            return result
        finally:
            self.sink(self.timer.summary(failed))

    def _run(self, opts: RunOptions) -> RunResult:
        input_dir, output_dir = opts.input_dir.resolve(), opts.output_dir.resolve()
        _check_folders(input_dir, output_dir)
        templates = load_templates(self.settings.templates_dirs)
        if opts.template_id and opts.template_id not in templates:
            raise MediaAnalysisError(
                f"Unknown template '{opts.template_id}'. Available: {', '.join(sorted(templates))}."
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        cache = Cache(output_dir / CACHE_DIR_NAME)
        force = opts.force_from

        ingest = self.ingest(input_dir)
        self.timer.audio_seconds = sum(f.duration for f in ingest.files if f.has_audio)
        transcript = self.transcribe(ingest, cache, self._forced("transcribe", force))
        scan = self.scan(ingest, cache, self._forced("scan", force))
        classification = self.classify(
            ingest, transcript, scan, templates, opts.template_id, cache, self._forced("classify", force)
        )
        template = templates[classification.template_id]  # type: ignore[index]
        plan = self.plan(ingest, transcript, scan, template, cache, self._forced("plan", force))
        candidates = self.extract(ingest, plan, cache, self._forced("extract", force))
        evidence = self.read(ingest, plan, candidates, template, cache, self._forced("read", force))
        draft = self.report(ingest, transcript, evidence, template, cache, self._forced("report", force))

        rep = Reporter(self.sink, "assemble")
        rep.started("writing output folder")
        report_path, warnings = assemble_output(output_dir, draft, evidence, ingest, template, classification)
        for w in warnings:
            rep.warning(w)
        rep.done(str(report_path))
        if opts.clean_cache:
            shutil.rmtree(cache.root, ignore_errors=True)
        return RunResult(report_path, classification, len(evidence.items), warnings)

    # 1. ingest ---------------------------------------------------------------------------------------------------

    def ingest(self, input_dir: Path) -> Ingest:
        rep = Reporter(self.sink, "ingest")
        rep.started(str(input_dir))
        files: list[MediaFile] = []
        skipped: list[str] = []
        for path in sorted(p for p in input_dir.iterdir() if p.is_file()):
            ext = path.suffix.lower()
            if ext not in VIDEO_EXTENSIONS and ext not in AUDIO_EXTENSIONS:
                skipped.append(path.name)
                continue
            info = ffmpeg.probe(path)
            if not info["has_audio"] and not info["has_video"]:
                skipped.append(path.name)
                continue
            files.append(MediaFile(
                id=f"f{len(files) + 1}", path=str(path), name=path.name, fingerprint=file_fingerprint(path), **info,
            ))
        for name in skipped:
            rep.warning(f"skipped (not video/audio): {name}")
        if not files:
            raise MediaAnalysisError(f"No video or audio files found in {input_dir}.")
        files = _label_speakers(files)
        labelled = [f for f in files if f.speaker]
        if labelled:
            rep.info("recorded together: " + ", ".join(f"{f.name} = {f.speaker}" for f in labelled))
        total = sum(f.duration for f in files)
        rep.done(f"{len(files)} file(s), {format_timecode(total)} total")
        return Ingest(files=files, skipped=skipped)

    # 2. transcribe -----------------------------------------------------------------------------------------------

    def transcribe(self, ingest: Ingest, cache: Cache, force: bool) -> Transcript:
        rep = Reporter(self.sink, "transcribe")
        ts = self.settings.transcription
        settings_key = ts.model_dump(exclude={"device"})
        keys = {f.id: digest([f.fingerprint, settings_key, STAGE_VERSIONS["transcribe"]]) for f in ingest.files}
        cached = {fid: cache.load("transcribe", k, FileTranscript) for fid, k in keys.items()}
        if not force and all(cached.values()):
            rep.skipped()
            return Transcript(files=[_relabel(cached[f.id], f.id) for f in ingest.files])  # type: ignore[arg-type]

        hw = detect()
        transcriber = FasterWhisperTranscriber(ts, hw)
        rep.started(f"model {ts.model} on {transcriber.device} ({transcriber.compute_type})")
        results: list[FileTranscript] = []
        total = sum(f.duration for f in ingest.files if f.has_audio) or 1.0
        done_before = 0.0
        for f in ingest.files:
            existing = None if force else cached[f.id]
            if existing:
                results.append(_relabel(existing, f.id))
                done_before += f.duration
                continue
            if not f.has_audio:
                ft = FileTranscript(file_id=f.id, language=None, segments=[])
            else:
                ft = self._transcribe_file(f, keys[f.id], transcriber, cache, force, rep, done_before, total)
                done_before += f.duration
            cache.save("transcribe", keys[f.id], ft)
            results.append(ft)
        rep.done(f"{sum(len(r.segments) for r in results)} segments")
        return Transcript(files=results)

    def _transcribe_file(self, f, file_key, transcriber, cache, force, rep, done_before, total) -> FileTranscript:
        rep.info(f"decoding audio: {f.name}")
        audio = ffmpeg.decode_audio(Path(f.path), SAMPLE_RATE)
        cuts = split_points(audio, self.settings.transcription.chunk_minutes * 60)
        segments = []
        languages: list[str] = []
        for i, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
            chunk_key = f"{file_key}-chunk{i}-{a}-{b}"
            chunk = None if force else cache.load("transcribe", chunk_key, FileTranscript)
            if chunk is None:
                offset = a / SAMPLE_RATE

                def progress(t: float, offset=offset) -> None:
                    rep.progress(done_before + offset + t, total, f.name)

                language, segs = transcriber.transcribe(audio[a:b], offset, progress)
                chunk = FileTranscript(file_id=f.id, language=language, segments=segs)
                cache.save("transcribe", chunk_key, chunk)
            else:
                rep.progress(done_before + b / SAMPLE_RATE, total, f"{f.name} (chunk {i + 1} cached)")
            segments.extend(chunk.segments)
            if chunk.language:
                languages.append(chunk.language)
        language = max(set(languages), key=languages.count) if languages else None
        return FileTranscript(file_id=f.id, language=language, segments=segments)

    # 3. scan -----------------------------------------------------------------------------------------------------

    def scan(self, ingest: Ingest, cache: Cache, force: bool) -> VisualScan:
        rep = Reporter(self.sink, "scan")
        videos = [f for f in ingest.files if f.has_video]
        if not videos:
            rep.skipped("no video")
            return VisualScan(segments=[])
        sc = self.settings.scan
        segments: list[VisualSegment] = []
        started = False
        for f in videos:
            key = digest([f.fingerprint, sc, STAGE_VERSIONS["scan"]])
            cached = None if force else cache.load("scan", key, VisualScan)
            if cached is None:
                if not started:
                    rep.started("local image analysis")
                    started = True
                raw = vision.scan_video(
                    Path(f.path), f.duration, sc.fps, sc.width, sc.height,
                    on_progress=lambda done, total, name=f.name: rep.progress(done, total, name),
                )
                cached = VisualScan(segments=[
                    VisualSegment(file_id=f.id, index=i + 1, start=s, end=e, kind=k, edge_density=d, rep_time=r)
                    for i, (s, e, k, d, r) in enumerate(raw)
                ])
                cache.save("scan", key, cached)
            segments.extend(s.model_copy(update={"file_id": f.id}) for s in cached.segments)
        if started:
            rep.done(f"{len(segments)} visual segments")
        else:
            rep.skipped()
        return VisualScan(segments=segments)

    # 4. classify -------------------------------------------------------------------------------------------------

    def classify(
        self, ingest: Ingest, transcript: Transcript, scan: VisualScan, templates: dict[str, Template],
        forced_id: str | None, cache: Cache, force: bool,
    ) -> Classification:
        rep = Reporter(self.sink, "classify")
        if forced_id:
            rep.skipped(f"template forced: {forced_id}")
            return Classification(template_id=forced_id, confidence=1.0, reason="Template selected by the user.", forced=True)
        fallback = next((t for t in templates.values() if t.fallback), None)
        if fallback is None:
            raise MediaAnalysisError("No fallback template found (the built-in 'generic' template is missing).")
        templates = {tid: t for tid, t in templates.items() if not t.fallback}
        key = digest([
            transcript, scan, [t.fingerprint_parts() for t in templates.values()],
            self.settings.llm.model, self.settings.llm.effort, STAGE_VERSIONS["classify"],
        ])
        result = None if force else cache.load("classify", key, Classification)
        if result is None:
            rep.started("asking Claude which template fits")
            content: list = [
                Text("# Available templates\n" + "\n".join(t.summary_for_classification() for t in templates.values())),
                Text("# Transcript\n" + transcript.to_prompt_text(ingest)),
                Text("# Visual segments\n" + scan.to_prompt_text()),
            ]
            sheet = self._contact_sheet(ingest, scan, cache)
            if sheet:
                content += [Text("# Contact sheet (thumbnails labelled with file id and time)"), Image(sheet)]
            data = self.llm.generate_json(prompts.CLASSIFY_SYSTEM, content, prompts.classify_schema(list(templates)), rep.activity)
            tid = data.get("template_id")
            suggested = data.get("suggested_template")
            result = Classification(
                template_id=tid if tid in templates else None,
                confidence=float(data.get("confidence", 0)),
                reason=data.get("reason", ""),
                closest=[c for c in data.get("closest", []) if c in templates],
                mismatch=[m for m in data.get("mismatch", []) if m],
                suggested=SuggestedTemplate(**suggested) if isinstance(suggested, dict) and suggested.get("id") else None,
            )
            cache.save("classify", key, result)
            cached_note = ""
        else:
            cached_note = ", cached"
        min_conf = self.settings.llm.classify_min_confidence
        if result.template_id is None or result.confidence < min_conf:
            # A weak best guess is still the closest match.
            closest = [result.template_id] if result.template_id else []
            closest += [c for c in result.closest if c not in closest]
            result = result.model_copy(update={"template_id": fallback.id, "closest": closest[:3], "fallback": True})
            rep.warning(f"no template fits: {result.reason}")
            rep.done(f"no template fits, writing a {fallback.name.lower()} report{cached_note}")
            return result
        rep.done(f"{result.template_id} (confidence {result.confidence:.2f}{cached_note})")
        return result

    def _contact_sheet(self, ingest: Ingest, scan: VisualScan, cache: Cache, limit: int = 16) -> Path | None:
        if not scan.segments:
            return None
        chosen = sorted(scan.segments, key=lambda s: (s.kind != "static", -(s.end - s.start)))[:limit]
        chosen.sort(key=lambda s: (s.file_id, s.start))
        thumbs_dir = cache.dir("scan", "thumbs")
        items = []
        for s in chosen:
            f = ingest.by_id(s.file_id)
            out = thumbs_dir / f"{f.fingerprint}-{int(s.rep_time * 1000)}.jpg"
            if not out.exists():
                ffmpeg.extract_still(Path(f.path), s.rep_time, out, max_side=640)
            items.append((f"{s.file_id} {format_timecode(s.rep_time)}", out))
        return vision.contact_sheet(items, cache.dir("scan") / f"sheet-{digest([str(p) for _, p in items])}.jpg")

    # 5. plan -----------------------------------------------------------------------------------------------------

    def plan(
        self, ingest: Ingest, transcript: Transcript, scan: VisualScan, template: Template, cache: Cache, force: bool,
    ) -> FramePlan:
        rep = Reporter(self.sink, "plan")
        videos = {f.id: f for f in ingest.files if f.has_video}
        if not videos:
            rep.skipped("no video")
            return FramePlan(requests=[])
        ev = self.settings.evidence
        key = digest([
            transcript, scan, template.fingerprint_parts(), self.settings.llm.model, self.settings.llm.effort, ev.max_requests,
            STAGE_VERSIONS["plan"],
        ])
        cached = None if force else cache.load("plan", key, FramePlan)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started("asking Claude which moments need frames")
        template_text = (
            f"Template: {template.name}\nSections: {', '.join(template.sections)}\n"
            "Visual evidence that matters:\n" + "\n".join(f"- {e}" for e in template.evidence)
        )
        video_list = "\n".join(f"- {f.id}: {f.name}, duration {f.duration:.0f}s" for f in videos.values())
        content = [
            Text("# Report template\n" + template_text),
            Text("# Files with video\n" + video_list),
            Text("# Transcript\n" + transcript.to_prompt_text(ingest)),
            Text("# Visual segments\n" + scan.to_prompt_text()),
            Text(f"Return at most {ev.max_requests} requests."),
        ]
        data = self.llm.generate_json(prompts.PLAN_SYSTEM, content, prompts.plan_schema(ev.max_requests), rep.activity)
        requests: list[EvidenceRequest] = []
        for r in data.get("requests", []):
            f = videos.get(r.get("file_id"))
            if f is None:
                continue
            start = max(0.0, min(float(r["start"]), f.duration))
            end = max(start, min(float(r["end"]), f.duration))
            if end - start < 2:
                start, end = max(0.0, start - 1), min(f.duration, end + 1)
            end = min(end, start + 60)
            requests.append(EvidenceRequest(
                file_id=f.id, start=start, end=end, reason=r.get("reason", ""), look_for=r.get("look_for", ""),
                priority=int(r.get("priority", 2)),
            ))
        requests.sort(key=lambda r: (r.priority, r.file_id, r.start))
        plan = FramePlan(requests=requests[: ev.max_requests])
        cache.save("plan", key, plan)
        rep.done(f"{len(plan.requests)} moment(s) requested")
        return plan

    # 6. extract --------------------------------------------------------------------------------------------------

    def extract(self, ingest: Ingest, plan: FramePlan, cache: Cache, force: bool) -> Candidates:
        rep = Reporter(self.sink, "extract")
        if not plan.requests:
            rep.skipped("nothing requested")
            return Candidates(candidates=[])
        ev = self.settings.evidence
        key = digest([plan, ev, [f.fingerprint for f in ingest.files], STAGE_VERSIONS["extract"]])
        cached = None if force else cache.load("extract", key, Candidates)
        if cached is not None and all(Path(c.path).exists() for c in cached.candidates):
            rep.skipped()
            return cached
        rep.started("picking the sharpest distinct frames")
        out_dir = cache.dir("extract", key)
        chosen_hashes: list[int] = []
        candidates: list[FrameCandidate] = []
        for ri, req in enumerate(plan.requests):
            rep.progress(ri, len(plan.requests))
            f = ingest.by_id(req.file_id)
            groups: list[list[tuple[float, int, float]]] = []
            for t, frame in ffmpeg.iter_frames(
                Path(f.path), ev.extract_fps, 640, 360, gray=True, start=req.start, duration=req.end - req.start,
            ):
                h, sharp = vision.dhash(frame), vision.sharpness(frame)
                if groups and vision.hamming(groups[-1][0][1], h) <= 6:
                    groups[-1].append((t, h, sharp))
                else:
                    groups.append([(t, h, sharp)])
            groups.sort(key=len, reverse=True)
            picked = 0
            for g in groups:
                if picked >= ev.max_candidates_per_request:
                    break
                t, h, sharp = max(g, key=lambda x: x[2])
                if any(vision.hamming(h, other) <= 4 for other in chosen_hashes):
                    continue
                cid = f"C{len(candidates) + 1:03d}"
                path = ffmpeg.extract_still(Path(f.path), t, out_dir / f"{cid}.jpg", max_side=ev.max_image_side)
                candidates.append(FrameCandidate(
                    id=cid, file_id=f.id, time=t, request_index=ri, path=str(path), sharpness=sharp,
                ))
                chosen_hashes.append(h)
                picked += 1
        result = Candidates(candidates=candidates)
        cache.save("extract", key, result)
        rep.done(f"{len(candidates)} candidate frame(s)")
        return result

    # 7. read -----------------------------------------------------------------------------------------------------

    def read(
        self, ingest: Ingest, plan: FramePlan, candidates: Candidates, template: Template, cache: Cache, force: bool,
    ) -> Evidence:
        rep = Reporter(self.sink, "read")
        if not candidates.candidates:
            rep.skipped("no frames")
            return Evidence(items=[])
        key = digest([candidates, plan, template.id, self.settings.llm.model, self.settings.llm.effort, STAGE_VERSIONS["read"]])
        cached = None if force else cache.load("read", key, Evidence)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started("asking Claude to check frames and read their text")
        batch_size = self.settings.evidence.vision_batch_size
        items: list[EvidenceItem] = []
        cands = candidates.candidates
        for b in range(0, len(cands), batch_size):
            rep.progress(b, len(cands))
            batch = cands[b: b + batch_size]
            content: list = [Text(f"Report type: {template.name}. Examine these {len(batch)} frames.")]
            for c in batch:
                req = plan.requests[c.request_index]
                f = ingest.by_id(c.file_id)
                content.append(Text(
                    f"Frame {c.id} — {f.id} ({f.name}) at {format_timecode(c.time)}. "
                    f"Requested because: {req.reason} Look for: {req.look_for}"
                ))
                content.append(Image(Path(c.path)))
            data = self.llm.generate_json(prompts.READ_SYSTEM, content, prompts.READ_SCHEMA, rep.activity)
            by_id = {c.id: c for c in batch}
            for fr in data.get("frames", []):
                c = by_id.get(fr.get("id"))
                if c is None or not fr.get("relevant"):
                    continue
                items.append(EvidenceItem(
                    id=f"E{len(items) + 1:02d}", file_id=c.file_id, time=c.time, path=c.path,
                    caption=fr.get("caption", "").strip(), description=fr.get("description", "").strip(),
                    text=fr.get("text", "").strip(),
                ))
        result = Evidence(items=items)
        cache.save("read", key, result)
        rep.done(f"{len(items)} of {len(cands)} frame(s) kept")
        return result

    # 8. report ---------------------------------------------------------------------------------------------------

    def report(
        self, ingest: Ingest, transcript: Transcript, evidence: Evidence, template: Template, cache: Cache, force: bool,
    ) -> ReportDraft:
        rep = Reporter(self.sink, "report")
        key = digest([
            transcript, evidence, template.fingerprint_parts(), self.settings.llm.model, self.settings.llm.effort, STAGE_VERSIONS["report"],
        ])
        cached = None if force else cache.load("report", key, ReportDraft)
        if cached is not None:
            rep.skipped()
            return cached
        rep.started(f"writing the {template.name.lower()} report")
        template_text = (
            f"Template: {template.name} — {template.description}\n"
            "Sections (H2, in this order):\n" + "\n".join(f"- {s}" for s in template.sections)
            + f"\n\nInstructions:\n{template.instructions}"
        )
        files_text = "\n".join(
            f"- {f.id}: {f.name} ({'video' if f.has_video else 'audio'}, {format_timecode(f.duration)})"
            for f in ingest.files
        )
        if evidence.items:
            ev_text = "\n\n".join(
                f"## {e.id} — {e.file_id} at {format_timecode(e.time)}\nCaption: {e.caption}\n"
                f"Description: {e.description}\nText in frame:\n{e.text or '(none)'}"
                for e in evidence.items
            )
        else:
            ev_text = "(no evidence frames)"
        content = [
            Text("# Template\n" + template_text),
            Text("# Files\n" + files_text),
            Text("# Evidence frames\n" + ev_text),
            Text("# Transcript\n" + transcript.to_prompt_text(ingest)),
        ]
        markdown = self.llm.generate_text(prompts.REPORT_SYSTEM, content, rep.activity).strip()
        if not markdown:
            raise MediaAnalysisError("Claude returned an empty report.")
        first = markdown.splitlines()[0]
        title = first[2:].strip() if first.startswith("# ") else template.name
        draft = ReportDraft(title=title, markdown=markdown)
        cache.save("report", key, draft)
        rep.done()
        return draft


class _TimedLLM:
    """Passes calls through to the real client and records how long each took."""

    def __init__(self, inner: LLM, timer: StageTimer) -> None:
        self._inner = inner
        self._timer = timer
        self.model = inner.model
        self.effort = inner.effort

    def generate_json(self, system, content, schema, on_activity=None):
        started = time.monotonic()
        try:
            return self._inner.generate_json(system, content, schema, on_activity)
        finally:
            self._timer.add_claude_call(time.monotonic() - started)

    def generate_text(self, system, content, on_activity=None):
        started = time.monotonic()
        try:
            return self._inner.generate_text(system, content, on_activity)
        finally:
            self._timer.add_claude_call(time.monotonic() - started)


# Meeting Recorder saves one folder per recording: video.mp4 (screen, no sound), audio-mic.wav (the person
# recording) and audio-system.wav (everyone heard through the computer), all starting at the same moment.
_SPEAKER_FILES = {"audio-mic": "Me", "audio-system": "Others"}


def _label_speakers(files: list[MediaFile]) -> list[MediaFile]:
    """Marks the audio files of a Meeting Recorder folder with their speaker, so their transcripts are merged."""
    speakers = {f.id: _SPEAKER_FILES.get(Path(f.name).stem.lower()) for f in files if f.has_audio}
    if not any(speakers.values()):
        return files
    return [f.model_copy(update={"speaker": speakers.get(f.id)}) for f in files]


def _relabel(ft: FileTranscript, file_id: str) -> FileTranscript:
    """Cached transcripts are keyed by content, so the same file may have had another id in an earlier run."""
    return ft.model_copy(update={"file_id": file_id})


def _check_folders(input_dir: Path, output_dir: Path) -> None:
    if not input_dir.is_dir():
        raise MediaAnalysisError(f"Input folder not found: {input_dir}")
    if output_dir == input_dir:
        raise MediaAnalysisError("Output folder must be different from the input folder.")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise MediaAnalysisError(f"Output path is not a folder: {output_dir}")
        entries = [p.name for p in output_dir.iterdir()]
        if entries and CACHE_DIR_NAME not in entries:
            raise MediaAnalysisError(
                f"Output folder {output_dir} is not empty and was not created by media-analysis. "
                "Use an empty or new folder."
            )

