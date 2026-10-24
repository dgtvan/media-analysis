from media_analysis.understand.models import FileTranscript, Ingest, MediaFile, Transcript, TranscriptSegment
from media_analysis.understand.sessions import label_speakers


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
    text = transcript.to_prompt_text(ingest)
    conversation = [line for line in text.splitlines() if line.startswith("[")]
    assert conversation == [
        "[00:00:05] Others: Can you share the diagram?",
        "[00:00:09] Me: Sure, here it is.",
        "[00:00:12] Others: Thanks.",
    ]
    assert "## f3: video.mp4" in text and "(no audio)" in text
