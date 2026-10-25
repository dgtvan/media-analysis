"""Stage 'sound': quiet stretches and loudness of every file whose sound is used (no AI)."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.workflow import Stage
from media_analysis.media import ffmpeg
from media_analysis.media.audio import silences
from media_analysis.understand.models import FileSound, Ingest, Sound

SAMPLE_RATE = 16000


class SoundStage(Stage):
    name = "sound"

    def run(self, ingest: Ingest) -> Sound:
        rep = self.rep
        audio_files = [f for f in ingest.files if f.uses_audio]
        if not audio_files:
            rep.skipped("no sound")
            return Sound(files=[])
        out: list[FileSound] = []
        started = False
        for n, f in enumerate(audio_files):
            key = self.key(f.fingerprint)
            cached = None if self.force else self.cache.load(self.name, key, FileSound)
            if cached is None:
                if not started:
                    rep.started("measuring pauses and loudness")
                    started = True
                rep.progress(n, len(audio_files), f.name)
                audio = ffmpeg.decode_audio(Path(f.path), SAMPLE_RATE)
                cached = FileSound(file_id=f.id, loudness=ffmpeg.loudness(Path(f.path)), silences=silences(audio, SAMPLE_RATE))
                self.cache.save(self.name, key, cached)
            out.append(cached.model_copy(update={"file_id": f.id}))
        if started:
            rep.done(f"{sum(len(s.silences) for s in out)} pauses in {len(out)} file(s)")
        else:
            rep.skipped()
        return Sound(files=out)
