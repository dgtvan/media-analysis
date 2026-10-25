from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, TranscriptSegment
from media_analysis.understand.sessions import label_speakers
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
