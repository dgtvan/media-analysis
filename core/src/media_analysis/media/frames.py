"""Single-frame image measures (no AI): blur, perceptual hash, sharpness, edges, changed pixels, contact sheets."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_KERNEL = np.ones((3, 3), np.uint8)


def blur(gray: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(gray, (5, 5), 0)


def dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def edges(blurred: np.ndarray) -> np.ndarray:
    return cv2.Canny(blurred, 50, 150) > 0


def changed_pixels(a_blur: np.ndarray, b_blur: np.ndarray, threshold: int, keep: np.ndarray | None = None) -> np.ndarray:
    """Pixels that differ between two blurred frames, with isolated specks removed."""
    diff = cv2.absdiff(a_blur, b_blur) > threshold
    if keep is not None:
        diff &= keep
    return cv2.morphologyEx(diff.astype(np.uint8), cv2.MORPH_OPEN, _KERNEL) > 0


def contact_sheet(items: list[tuple[str, Path]], out: Path, columns: int = 4, cell: tuple[int, int] = (384, 216)) -> Path:
    """Grid of labelled thumbnails in one image, so the model gets visual context in a single picture."""
    rows = (len(items) + columns - 1) // columns
    label_h = 22
    sheet = Image.new("RGB", (columns * cell[0], rows * (cell[1] + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    for i, (label, path) in enumerate(items):
        x, y = (i % columns) * cell[0], (i // columns) * (cell[1] + label_h)
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail(cell)
            sheet.paste(im, (x + (cell[0] - im.width) // 2, y + label_h))
        draw.text((x + 4, y + 2), label, fill="black", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=85)
    return out
