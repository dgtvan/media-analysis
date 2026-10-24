"""Stage 'extract': decodes each requested range, groups near-duplicate frames and keeps the sharpest distinct ones."""

from __future__ import annotations

from pathlib import Path

from media_analysis.engine.workflow import Stage
from media_analysis.media import ffmpeg
from media_analysis.media.frames import dhash, hamming, sharpness
from media_analysis.understand.models import Ingest
from media_analysis.workflows.report.models import Candidates, FrameCandidate, FramePlan


class ExtractStage(Stage):
    name = "extract"

    def run(self, ingest: Ingest, plan: FramePlan) -> Candidates:
        rep = self.rep
        if not plan.requests:
            rep.skipped("nothing requested")
            return Candidates(candidates=[])
        ev = self.settings.evidence
        key = self.key(plan, ev, [f.fingerprint for f in ingest.files])
        cached = None if self.force else self.cache.load(self.name, key, Candidates)
        if cached is not None and all(Path(c.path).exists() for c in cached.candidates):
            rep.skipped()
            return cached
        rep.started("picking the sharpest distinct frames")
        out_dir = self.cache.dir(self.name, key)
        chosen_hashes: list[int] = []
        candidates: list[FrameCandidate] = []
        for ri, req in enumerate(plan.requests):
            rep.progress(ri, len(plan.requests))
            f = ingest.by_id(req.file_id)
            groups: list[list[tuple[float, int, float]]] = []
            for t, frame in ffmpeg.iter_frames(
                Path(f.path), ev.extract_fps, 640, 360, gray=True, start=req.start, duration=req.end - req.start,
            ):
                h, sharp = dhash(frame), sharpness(frame)
                if groups and hamming(groups[-1][0][1], h) <= 6:
                    groups[-1].append((t, h, sharp))
                else:
                    groups.append([(t, h, sharp)])
            groups.sort(key=len, reverse=True)
            picked = 0
            for g in groups:
                if picked >= ev.max_candidates_per_request:
                    break
                t, h, sharp = max(g, key=lambda x: x[2])
                if any(hamming(h, other) <= 4 for other in chosen_hashes):
                    continue
                cid = f"C{len(candidates) + 1:03d}"
                path = ffmpeg.extract_still(Path(f.path), t, out_dir / f"{cid}.jpg", max_side=ev.max_image_side)
                candidates.append(FrameCandidate(
                    id=cid, file_id=f.id, time=t, request_index=ri, path=str(path), sharpness=sharp,
                ))
                chosen_hashes.append(h)
                picked += 1
        result = Candidates(candidates=candidates)
        self.cache.save(self.name, key, result)
        rep.done(f"{len(candidates)} candidate frame(s)")
        return result
