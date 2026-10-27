import json

from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, TranscriptSegment
from media_analysis.understand.sessions import label_speakers, read_manifest
from media_analysis.understand.views import transcript_view


def media(fid, name, video=False, audio=True, duration=60.0):
    return MediaFile(id=fid, path=name, name=name, fingerprint=fid, duration=duration, has_video=video, has_audio=audio)


def meeting_recorder_folder():
    return [media("f1", "audio-mic.wav"), media("f2", "audio-system.wav"), media("f3", "video.mp4", video=True, audio=False)]


def test_meeting_recorder_files_get_speakers():
    files = label_speakers(meeting_recorder_folder())
    assert [f.speaker for f in files] == ["Me", "Others", None]
    plain = label_speakers([media("f1", "talk.mp3"), media("f2", "part2.mp3")])
    assert all(f.speaker is None for f in plain)


def test_recorded_together_transcripts_merge_into_one_conversation():
    ingest = Ingest(files=label_speakers(meeting_recorder_folder()))
    transcript = Transcript(files=[
        FileTranscript(file_id="f1", language="en", segments=[TranscriptSegment(start=9, end=11, text="Sure, here it is.")]),
        FileTranscript(file_id="f2", language="en", segments=[
            TranscriptSegment(start=5, end=8, text="Can you share the diagram?"),
            TranscriptSegment(start=12, end=14, text="Thanks."),
        ]),
        FileTranscript(file_id="f3", language=None, segments=[]),
    ])
    text = transcript_view(ingest, transcript)
    conversation = [line for line in text.splitlines() if line.startswith("[")]
    assert conversation == [
        "[00:00:05-00:00:08] Others: Can you share the diagram?",
        "[00:00:09-00:00:11] Me: Sure, here it is.",
        "[00:00:12-00:00:14] Others: Thanks.",
    ]
    assert "## f3: video.mp4" in text and "(no audio)" in text


def test_a_recorder_video_with_sound_is_not_transcribed_again():
    files = label_speakers([media("f1", "audio-mic.wav"), media("f2", "audio-system.wav"), media("f3", "video.mp4", video=True)])
    assert [f.duplicate_audio for f in files] == [False, False, True]
    assert [f.uses_audio for f in files] == [True, True, False]
    ingest = Ingest(files=files)
    transcript = Transcript(files=[
        FileTranscript(file_id="f1", language="en", segments=[TranscriptSegment(start=1, end=2, text="Hi.")]),
        FileTranscript(file_id="f2", language="en", segments=[]),
        FileTranscript(file_id="f3", language=None, segments=[]),
    ])
    assert "isn't repeated here" in transcript_view(ingest, transcript)
    plain = label_speakers([media("f1", "talk.mp4", video=True)])
    assert not plain[0].duplicate_audio


def test_audio_app_of_older_recordings_is_not_transcribed_again():
    files = label_speakers([media("f1", "audio-app.m4a"), media("f2", "audio-mic.m4a"), media("f3", "audio-system.m4a")])
    assert [f.speaker for f in files] == [None, "Me", "Others"]
    assert [f.uses_audio for f in files] == [False, True, True]


def test_recording_json_gives_speakers_offsets_and_silent_sides(tmp_path):
    (tmp_path / "recording.json").write_text(json.dumps({"schema": 1, "files": [
        {"file": "video.mp4", "kind": "video", "start_seconds": 1.7},
        {"file": "audio-system.m4a", "kind": "system", "speaker": "others", "start_seconds": 0, "silent": True},
        {"file": "audio-mic.m4a", "kind": "microphone", "speaker": "me", "start_seconds": 0, "silent": False},
    ]}), encoding="utf-8")
    manifest = read_manifest(tmp_path)
    files = label_speakers(
        [media("f1", "audio-mic.m4a"), media("f2", "audio-system.m4a"), media("f3", "video.mp4", video=True)], manifest,
    )
    assert [(f.speaker, f.offset, f.uses_audio) for f in files] == [("Me", 0, True), ("Others", 0, False), (None, 1.7, False)]
    ingest = Ingest(files=files)
    transcript = Transcript(files=[
        FileTranscript(file_id="f1", language="en", segments=[TranscriptSegment(start=3, end=5, text="Hello?")]),
        FileTranscript(file_id="f2", language=None, segments=[]),
        FileTranscript(file_id="f3", language=None, segments=[]),
    ])
    text = transcript_view(ingest, transcript)
    assert "Only one side of this conversation was recorded: Others (audio-system.m4a)" in text
    assert "## f3: video.mp4 (duration 00:01:00, starts at 00:00:01 on the shared timeline)" in text
    assert read_manifest(tmp_path / "missing") == {}


def test_times_of_a_later_file_move_onto_the_shared_clock():
    files = [media("f1", "audio-mic.wav"), media("f2", "audio-system.wav")]
    files[1] = files[1].model_copy(update={"offset": 10.0})
    ingest = Ingest(files=label_speakers(files))
    transcript = Transcript(files=[
        FileTranscript(file_id="f1", language="en", segments=[TranscriptSegment(start=12, end=13, text="Yes.")]),
        FileTranscript(file_id="f2", language="en", segments=[TranscriptSegment(start=1, end=2, text="Ready?")]),
    ])
    conversation = [line for line in transcript_view(ingest, transcript).splitlines() if line.startswith("[")]
    assert conversation == ["[00:00:11-00:00:12] Others: Ready?", "[00:00:12-00:00:13] Me: Yes."]
