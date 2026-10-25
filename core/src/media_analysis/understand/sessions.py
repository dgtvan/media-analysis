"""Files recorded together on one timeline (Meeting Recorder folders)."""

from __future__ import annotations

from pathlib import Path

from media_analysis.understand.models import MediaFile

# Meeting Recorder saves one folder per recording: video.mp4 (the screen, sometimes with the mixed sound), audio-mic
# (the person recording) and audio-system (everyone heard through the computer), all starting at the same moment.
SPEAKER_FILES = {"audio-mic": "Me", "audio-system": "Others"}


def label_speakers(files: list[MediaFile]) -> list[MediaFile]:
    """Marks the audio files of a Meeting Recorder folder with their speaker, so their transcripts are merged. A video
    recorded with them that has sound only repeats their mix, so its sound is left out: transcribing it would take
    as long again and give Claude the whole conversation twice, without speakers."""
    speakers = {f.id: SPEAKER_FILES.get(Path(f.name).stem.lower()) for f in files if f.has_audio}
    if not any(speakers.values()):
        return files
    return [
        f.model_copy(update={
            "speaker": speakers.get(f.id),
            "duplicate_audio": f.has_video and f.has_audio and not speakers.get(f.id),
        })
        for f in files
    ]
