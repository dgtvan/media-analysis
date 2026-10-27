"""Stage 'ingest': lists the folder's video and audio files and probes them."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.cache import file_fingerprint
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.timecode import format_timecode
from media_analysis.engine.workflow import Stage
from media_analysis.media import ffmpeg
from media_analysis.understand.models import Ingest, MediaFile
from media_analysis.understand.sessions import MANIFEST_FILE, label_speakers, one_sided, read_manifest

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr"}


class IngestStage(Stage):
    name = "ingest"

    def run(self, input_dir: Path) -> Ingest:
        """The folder's files in name order."""
        rep = self.rep
        rep.started(str(input_dir))
        files: list[MediaFile] = []
        skipped: list[str] = []
        for path in sorted(p for p in input_dir.iterdir() if p.is_file()):
            if path.name == MANIFEST_FILE:
                continue
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
        manifest = read_manifest(input_dir)
        if manifest:
            rep.info(f"using {MANIFEST_FILE}")
        files = label_speakers(files, manifest)
        labelled = [f for f in files if f.speaker]
        if labelled:
            rep.info("recorded together: " + ", ".join(f"{f.name} = {f.speaker}" for f in labelled))
        if not_used := [f.name for f in files if f.has_audio and not f.uses_audio]:
            rep.info("sound not transcribed (silent or repeated in other files): " + ", ".join(not_used))
        if note := one_sided(files):
            rep.warning(note)
        total = sum(f.duration for f in files)
        rep.done(f"{len(files)} file(s), {format_timecode(total)} total")
        return Ingest(files=files, skipped=skipped)
