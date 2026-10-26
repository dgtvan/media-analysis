from media_analysis.understand.languages import describe, language_shares, main_languages, transcript_note
from media_analysis.understand.models import FileTranscript, Transcript, TranscriptSegment


def seg(start, end, language=None):
    return TranscriptSegment(start=start, end=end, text="x", language=language)


def test_shares_use_segment_languages_and_fall_back_to_the_file_language():
    t = Transcript(files=[
        FileTranscript(file_id="f1", language="vi", segments=[seg(0, 60), seg(60, 80, "en")]),
        FileTranscript(file_id="f2", language=None, segments=[seg(0, 20, "vi"), seg(20, 21, "fi")]),
    ])
    shares = language_shares(t)
    assert list(shares) == ["vi", "en", "fi"]
    assert describe(shares) == "Vietnamese 79%, English 20%"
    assert main_languages(shares) == ["vi"]  # English is just under the 20% a second main language needs


def test_note_for_claude_only_when_speech_is_not_all_english():
    assert transcript_note({"en": 0.98, "fi": 0.02}) is None
    note = transcript_note({"vi": 0.85, "en": 0.15})
    assert note.startswith("Speech: Vietnamese 85%, English 15% (detected).")
    assert "English technical terms and names inside Vietnamese speech are often misheard" in note
