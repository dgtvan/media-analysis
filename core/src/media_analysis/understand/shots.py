"""Stage 'shots': splits camera footage into shots and measures how usable each one is (no AI). Screen recordings use
the 'scan' stage instead."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from media_analysis.engine.workflow import Stage
from media_analysis.media.shots import ShotParams, detect_shots
from media_analysis.understand.models import Ingest, Shot, ShotList


class ShotsStage(Stage):
    name = "shots"

    def run(self, ingest: Ingest, params: ShotParams = ShotParams()) -> ShotList:
        rep = self.rep
        videos = [f for f in ingest.files if f.has_video]
        if not videos:
            rep.skipped("no video")
            return ShotList(shots=[])
        shots: list[Shot] = []
        started = False
        for n, f in enumerate(videos):
            key = self.key(f.fingerprint, asdict(params))
            cached = None if self.force else self.cache.load(self.name, key, ShotList)
            if cached is None:
                if not started:
                    rep.started("finding shots and measuring quality")
                    started = True
                raw = detect_shots(
                    Path(f.path), f.duration, params,
                    on_progress=lambda done, total, name=f.name, n=n: rep.progress(n + done / total, len(videos), name),
                )
                cached = ShotList(shots=[
                    Shot(file_id=f.id, index=i + 1, start=s.start, end=s.end, sharpness=s.sharpness, shake=s.shake,
                         brightness=s.brightness, clipped=s.clipped, rep_time=s.rep_time)
                    for i, s in enumerate(raw)
                ])
                self.cache.save(self.name, key, cached)
            shots.extend(s.model_copy(update={"file_id": f.id}) for s in cached.shots)
        if started:
            rep.done(f"{len(shots)} shot(s) in {len(videos)} clip(s)")
        else:
            rep.skipped()
        return ShotList(shots=shots)
