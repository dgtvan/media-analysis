import numpy as np
import cv2

from media_analysis.media import segmentation
from media_analysis.media.frames import blur, changed_pixels, dhash, hamming

H, W = 180, 320


def slide(lines: int, clock: int = 0) -> np.ndarray:
    img = np.full((H, W), 255, np.uint8)
    cv2.putText(img, "Title", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
    for i in range(lines):
        cv2.putText(img, f"line {i} some code here", (10, 55 + i * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 1)
    cv2.putText(img, f"{clock:02d}", (285, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 100, 1)
    return img


def camera(t: int) -> np.ndarray:
    img = np.full((H, W), 80, np.uint8)
    x = int(160 + 50 * np.sin(t))
    cv2.circle(img, (x, 90), 40, 200, -1)
    return img


def run(frames: list[np.ndarray]) -> list[segmentation.RawSegment]:
    p = segmentation.ScanParams()
    blurred = [blur(f) for f in frames]
    motion = [0.0] + [float(changed_pixels(b, a, p.pixel_threshold).mean()) for a, b in zip(blurred, blurred[1:])]
    bh, bw = H // p.block, W // p.block
    blocks = [np.zeros((bh, bw), bool)]
    for a, b in zip(blurred, blurred[1:]):
        c = changed_pixels(b, a, p.pixel_threshold)
        blocks.append(c[: bh * p.block, : bw * p.block].reshape(bh, p.block, bw, p.block).any(axis=(1, 3)))
    hists = []
    for f in frames:
        h = cv2.calcHist([f], [0], None, [32], [0, 256])
        hists.append(cv2.normalize(h, h).flatten())
    return segmentation.segment_frames(np.array(frames), np.array(motion), np.array(blocks), hists, p)


def test_ticking_clock_does_not_split_a_slide():
    segs = run([slide(4, clock=i) for i in range(20)])
    assert len(segs) == 1
    assert segs[0].kind == "static"


def test_added_line_splits_slide():
    frames = [slide(4, i) for i in range(10)] + [slide(5, i) for i in range(10, 20)]
    segs = run(frames)
    assert [s.start_index for s in segs] == [0, 10]


def test_camera_then_slide():
    frames = [camera(i) for i in range(10)] + [slide(4, i) for i in range(10)]
    segs = run(frames)
    assert [(s.start_index, s.kind) for s in segs] == [(0, "moving"), (10, "static")]


def test_dhash_and_hamming():
    a = slide(4)
    assert hamming(dhash(a), dhash(a.copy())) == 0
    assert hamming(dhash(a), dhash(camera(0))) > 4
