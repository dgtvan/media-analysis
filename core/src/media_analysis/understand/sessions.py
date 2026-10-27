"""Files recorded together on one timeline (Meeting Recorder folders)."""

from __future__ import annotations

import json
from pathlib import Path

from media_analysis.understand.models import MediaFile

# Meeting Recorder saves one folder per recording: video.mp4 (the screen, sometimes with the mixed sound), audio-mic
# (the person recording) and audio-system (everyone heard through the computer), all starting at the same moment.
SPEAKER_FILES = {"audio-mic": "Me", "audio-system": "Others"}
# Recordings of 26-27 Oct 2026 also have audio-app, the meeting app's own sound: part of audio-system.
DUPLICATE_OF = {"audio-app": "audio-system"}

MANIFEST_FILE = "recording.json"
"""Written by Meeting Recorder next to README.md: per file its speaker, start on the shared clock and measured sound."""
MANIFEST_SPEAKERS = {"me": "Me", "others": "Others"}


def read_manifest(input_dir: Path) -> dict[str, dict]:
    """The files of the folder's recording.json by name; empty when there is none or it can't be read."""
    path = input_dir / MANIFEST_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {f["file"]: f for f in data.get("files", []) if isinstance(f, dict) and f.get("file")}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def label_speakers(files: list[MediaFile], manifest: dict[str, dict] | None = None) -> list[MediaFile]:
    """Marks the audio files of a Meeting Recorder folder with their speaker, so their transcripts are merged, and
    what of them not to transcribe. From recording.json when there is one (speaker, start offset, silent); otherwise
    from the file names. A video recorded with them that has sound only repeats their mix, so its sound is left out:
    transcribing it would take as long again and give Claude the whole conversation twice, without speakers."""
    if manifest:
        files = [_from_manifest(f, manifest.get(f.name)) for f in files]
        speakers = {f.id: f.speaker for f in files}
    else:
        speakers = {f.id: SPEAKER_FILES.get(Path(f.name).stem.lower()) for f in files if f.has_audio}
    if not any(speakers.values()):
        return files
    stems = {Path(f.name).stem.lower() for f in files}
    return [
        f.model_copy(update={
            "speaker": speakers.get(f.id),
            "duplicate_audio": f.has_audio and not speakers.get(f.id)
            and (f.has_video or DUPLICATE_OF.get(Path(f.name).stem.lower()) in stems),
        })
        for f in files
    ]


def _from_manifest(f: MediaFile, entry: dict | None) -> MediaFile:
    if not entry:
        return f
    update: dict = {"speaker": MANIFEST_SPEAKERS.get(str(entry.get("speaker", "")).lower())}
    if isinstance(entry.get("start_seconds"), (int, float)):
        update["offset"] = float(entry["start_seconds"])
    if entry.get("silent") is True:
        update["silent"] = True
    return f.model_copy(update=update)


def one_sided(files: list[MediaFile]) -> str | None:
    """A note when a recorded-together conversation has a silent side, e.g. the other participants weren't captured."""
    silent = [f for f in files if f.speaker and f.silent]
    heard = [f for f in files if f.speaker and not f.silent]
    if not silent or not heard:
        return None
    missing = ", ".join(f"{f.speaker} ({f.name})" for f in silent)
    return (f"Only one side of this conversation was recorded: {missing} is silent. What they said is missing, so "
            "the transcript shows only what was heard on the other files.")
