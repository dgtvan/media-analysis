"""Stage 'transcribe': speech to text per file, in chunks cut at quiet moments, each chunk cached so an interrupted
run resumes mid-file."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.hardware import detect
from media_analysis.engine.workflow import Stage
from media_analysis.media import ffmpeg
from media_analysis.providers.transcriber import SAMPLE_RATE, FasterWhisperTranscriber, split_points
from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, TranscriptSegment


class TranscribeStage(Stage):
    name = "transcribe"

    def run(self, ingest: Ingest) -> Transcript:
        rep, cache, force = self.rep, self.cache, self.force
        ts = self.settings.transcription
        settings_key = ts.model_dump(exclude={"device"})
        keys = {f.id: self.key(f.fingerprint, settings_key) for f in ingest.files}
        cached = {fid: cache.load(self.name, k, FileTranscript) for fid, k in keys.items()}
        if not force and all(cached.values()):
            rep.skipped()
            return Transcript(files=[_relabel(cached[f.id], f.id) for f in ingest.files])  # type: ignore[arg-type]

        transcriber = FasterWhisperTranscriber(ts, detect())
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
                ft = self._transcribe_file(f, keys[f.id], transcriber, done_before, total)
                done_before += f.duration
            cache.save(self.name, keys[f.id], ft)
            results.append(ft)
        rep.done(f"{sum(len(r.segments) for r in results)} segments")
        return Transcript(files=results)

    def _transcribe_file(
        self, f: MediaFile, file_key: str, transcriber: FasterWhisperTranscriber, done_before: float, total: float,
    ) -> FileTranscript:
        rep = self.rep
        rep.info(f"decoding audio: {f.name}")
        audio = ffmpeg.decode_audio(Path(f.path), SAMPLE_RATE)
        cuts = split_points(audio, self.settings.transcription.chunk_minutes * 60)
        segments: list[TranscriptSegment] = []
        languages: list[str] = []
        for i, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
            chunk_key = f"{file_key}-chunk{i}-{a}-{b}"
            chunk = None if self.force else self.cache.load(self.name, chunk_key, FileTranscript)
            if chunk is None:
                offset = a / SAMPLE_RATE

                def progress(t: float, offset=offset) -> None:
                    rep.progress(done_before + offset + t, total, f.name)

                language, segs = transcriber.transcribe(audio[a:b], offset, progress)
                chunk = FileTranscript(file_id=f.id, language=language, segments=[
                    TranscriptSegment(start=start, end=end, text=text) for start, end, text in segs
                ])
                self.cache.save(self.name, chunk_key, chunk)
            else:
                rep.progress(done_before + b / SAMPLE_RATE, total, f"{f.name} (chunk {i + 1} cached)")
            segments.extend(chunk.segments)
            if chunk.language:
                languages.append(chunk.language)
        language = max(set(languages), key=languages.count) if languages else None
        return FileTranscript(file_id=f.id, language=language, segments=segments)


def _relabel(ft: FileTranscript, file_id: str) -> FileTranscript:
    """Cached transcripts are keyed by content, so the same file may have had another id in an earlier run."""
    return ft.model_copy(update={"file_id": file_id})
