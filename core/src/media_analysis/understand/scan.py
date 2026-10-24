"""Stage 'scan': splits each video into visual segments with local image analysis (no AI)."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.workflow import Stage
from media_analysis.media import segmentation
from media_analysis.understand.models import Ingest, VisualScan, VisualSegment


class ScanStage(Stage):
    name = "scan"
    version = 2

    def run(self, ingest: Ingest) -> VisualScan:
        rep = self.rep
        videos = [f for f in ingest.files if f.has_video]
        if not videos:
            rep.skipped("no video")
            return VisualScan(segments=[])
        sc = self.settings.scan
        segments: list[VisualSegment] = []
        started = False
        for f in videos:
            key = self.key(f.fingerprint, sc)
            cached = None if self.force else self.cache.load(self.name, key, VisualScan)
            if cached is None:
                if not started:
                    rep.started("local image analysis")
                    started = True
                raw = segmentation.scan_video(
                    Path(f.path), f.duration, sc.fps, sc.width, sc.height,
                    on_progress=lambda done, total, name=f.name: rep.progress(done, total, name),
                )
                cached = VisualScan(segments=[
                    VisualSegment(file_id=f.id, index=i + 1, start=s, end=e, kind=k, edge_density=d, rep_time=r)
                    for i, (s, e, k, d, r) in enumerate(raw)
                ])
                self.cache.save(self.name, key, cached)
            segments.extend(s.model_copy(update={"file_id": f.id}) for s in cached.segments)
        if started:
            rep.done(f"{len(segments)} visual segments")
        else:
            rep.skipped()
        return VisualScan(segments=segments)
