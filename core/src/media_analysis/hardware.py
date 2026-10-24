"""Detect what the machine can use for transcription."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Hardware:
    cuda_devices: int
    cpu_threads: int
    gpus: list[str]

    @property
    def device(self) -> str:
        return "cuda" if self.cuda_devices > 0 else "cpu"

    @property
    def compute_type(self) -> str:
        return "float16" if self.device == "cuda" else "int8"


def _cuda_device_count() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def _list_gpus() -> list[str]:
    """Names of display adapters, for information only (faster-whisper can use NVIDIA/CUDA GPUs only)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def detect(list_gpus: bool = False) -> Hardware:
    return Hardware(
        cuda_devices=_cuda_device_count(),
        cpu_threads=os.cpu_count() or 1,
        gpus=_list_gpus() if list_gpus else [],
    )
