import io
import json
import urllib.error

import numpy as np
import pytest

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.settings import AZURE_KEY_ENV, TranscriptionSettings, read_terms
from media_analysis.providers import transcriber as tr
from media_analysis.providers.transcriber import (
    SAMPLE_RATE, AzureSpeechTranscriber, FasterWhisperTranscriber, azure_url, create_transcriber,
)


def test_azure_url_uses_only_the_host():
    assert azure_url(" https://my-res.cognitiveservices.azure.com/anything?x=1 ") == (
        "https://my-res.cognitiveservices.azure.com/speechtotext/transcriptions:transcribe?api-version=2025-10-15")


@pytest.mark.parametrize("endpoint", ["my-res.cognitiveservices.azure.com", "http://my-res.cognitiveservices.azure.com/", ""])
def test_azure_url_rejects_non_https(endpoint):
    with pytest.raises(MediaAnalysisError):
        azure_url(endpoint)


def test_terms_file_skips_comments_blanks_and_repeats(tmp_path):
    f = tmp_path / "terms.txt"
    f.write_text("﻿# comment\nclient ID\n\n  subscription  \nclient ID\n", encoding="utf-8")
    assert read_terms(f) == ["client ID", "subscription"]
    assert read_terms(tmp_path / "missing.txt") == []


def test_azure_cache_key_ignores_local_settings_and_follows_the_terms(tmp_path):
    terms = tmp_path / "terms.txt"
    a = TranscriptionSettings(engine="azure-speech", azure_endpoint="https://a.cognitiveservices.azure.com/", model="small",
                              terms_file=terms)
    b = TranscriptionSettings(engine="azure-speech", azure_endpoint="https://b.cognitiveservices.azure.com/", models_dir=None,
                              terms_file=terms)
    assert a.cache_key() == b.cache_key()
    assert a.cache_key() != TranscriptionSettings().cache_key()
    terms.write_text("endpoint\n", encoding="utf-8")
    assert "terms" in a.cache_key()
    assert TranscriptionSettings(models_dir=None, terms_file=terms).cache_key() == TranscriptionSettings(models_dir=None).cache_key()


def test_factory_picks_the_engine(monkeypatch):
    monkeypatch.setenv(AZURE_KEY_ENV, "k")
    azure = create_transcriber(
        TranscriptionSettings(engine="azure-speech", azure_endpoint="https://a.cognitiveservices.azure.com/", terms_file=None), bytes)
    assert isinstance(azure, AzureSpeechTranscriber)
    assert azure.locales == ["en-US", "vi-VN"]
    assert "a.cognitiveservices.azure.com" in azure.description
    assert isinstance(create_transcriber(TranscriptionSettings(), bytes), FasterWhisperTranscriber)


def test_azure_needs_a_key(monkeypatch):
    monkeypatch.delenv(AZURE_KEY_ENV, raising=False)
    with pytest.raises(MediaAnalysisError, match="no Azure key"):
        AzureSpeechTranscriber(TranscriptionSettings(engine="azure-speech", azure_endpoint="https://a.cognitiveservices.azure.com/"), bytes)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def azure(monkeypatch, tmp_path, terms: str = "", **settings) -> AzureSpeechTranscriber:
    monkeypatch.setenv(AZURE_KEY_ENV, "secret")
    f = tmp_path / "terms.txt"
    f.write_text(terms, encoding="utf-8")
    ts = TranscriptionSettings(engine="azure-speech", azure_endpoint="https://a.cognitiveservices.azure.com/", terms_file=f, **settings)
    return AzureSpeechTranscriber(ts, lambda audio: b"FLAC")


def test_azure_transcribe_shifts_times_and_keeps_words(monkeypatch, tmp_path):
    sent = {}

    def urlopen(request, timeout):
        sent["headers"] = dict(request.header_items())
        sent["body"] = request.data
        return FakeResponse(json.dumps({"phrases": [
            {"offsetMilliseconds": 0, "durationMilliseconds": 2000, "text": " Xin chào ", "locale": "vi-VN",
             "words": [{"text": "Xin", "offsetMilliseconds": 100, "durationMilliseconds": 400},
                       {"text": "chào", "offsetMilliseconds": 600, "durationMilliseconds": 400}]},
            {"offsetMilliseconds": 2500, "durationMilliseconds": 1500, "text": "Ờ, okay", "locale": "en-US",
             "words": [{"text": "Ờ,", "offsetMilliseconds": 2500, "durationMilliseconds": 300},
                       {"text": "okay", "offsetMilliseconds": 3000, "durationMilliseconds": 500}]},
            {"offsetMilliseconds": 4000, "durationMilliseconds": 100, "text": " ", "locale": "vi-VN"},
        ]}).encode())

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    progress = []
    t = azure(monkeypatch, tmp_path, "client ID\nendpoint\n", word_timestamps=True, language="vi")
    language, segs = t.transcribe(np.zeros(SAMPLE_RATE * 4, np.float32), 60.0, progress.append)
    assert language == "vi"
    assert segs == [
        (60.0, 62.0, "Xin chào", [(60.1, 60.5, "Xin"), (60.6, 61.0, "chào")], "vi"),
        (62.5, 64.0, "Ờ, okay", [(62.5, 62.8, "Ờ,"), (63.0, 63.5, "okay")], "en"),
    ]
    assert progress == [4.0]
    assert sent["headers"]["Ocp-apim-subscription-key"] == "secret"
    definition = json.loads(sent["body"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
    assert definition == {"locales": ["vi-VN"], "profanityFilterMode": "None",
                          "phraseList": {"phrases": ["client ID", "endpoint"]}}
    assert b"FLAC" in sent["body"]


def test_azure_without_word_times_returns_none_words(monkeypatch, tmp_path):
    monkeypatch.setattr(tr.urllib.request, "urlopen", lambda request, timeout: FakeResponse(json.dumps({"phrases": [
        {"offsetMilliseconds": 1000, "durationMilliseconds": 1000, "text": "Hi", "locale": "en-US",
         "words": [{"text": "Hi", "offsetMilliseconds": 1000, "durationMilliseconds": 500}]},
    ]}).encode()))
    language, segs = azure(monkeypatch, tmp_path).transcribe(np.zeros(SAMPLE_RATE, np.float32), 0.0)
    assert language == "en"
    assert segs == [(1.0, 2.0, "Hi", None, "en")]


def sent_definition(request) -> dict:
    """The definition field of a multipart request (the first part)."""
    return json.loads(request.data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])


def http_error(code: int, body: dict, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(json.dumps(body).encode()))


def test_azure_waits_out_rate_limits(monkeypatch, tmp_path):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise http_error(429, {"error": {"message": "slow down"}}, {"retry-after": "0"})
        return FakeResponse(b'{"phrases": []}')

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)
    assert azure(monkeypatch, tmp_path, language="vi").transcribe(np.zeros(SAMPLE_RATE, np.float32), 0.0) == (None, [])
    assert len(calls) == 3


def test_azure_chunk_without_speech_is_empty(monkeypatch, tmp_path):
    def urlopen(request, timeout):
        raise http_error(422, {"code": "UnprocessableEntity", "message": "No language was identified."})

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    assert azure(monkeypatch, tmp_path).transcribe(np.zeros(SAMPLE_RATE, np.float32), 0.0) == (None, [])


@pytest.mark.parametrize("code, body, expected", [
    (401, {"error": {"code": "401", "message": "Access denied due to invalid subscription key."}}, "rejected the key.*invalid subscription key"),
    (403, {"error": {"code": "403", "message": "Access denied due to Virtual Network/Firewall rules."}}, "firewall refused.*Add your client IP address"),
])
def test_azure_errors_explain_themselves(monkeypatch, tmp_path, code, body, expected):
    def urlopen(request, timeout):
        raise http_error(code, body)

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    with pytest.raises(MediaAnalysisError, match=expected):
        azure(monkeypatch, tmp_path).transcribe(np.zeros(SAMPLE_RATE, np.float32), 0.0)


def test_azure_detect_transcribes_pieces_in_each_language_and_keeps_the_more_confident(monkeypatch, tmp_path):
    requests = []

    def urlopen(request, timeout):
        definition = sent_definition(request)
        seconds = request.data.count(b"X") / SAMPLE_RATE
        (locale,) = definition["locales"]
        requests.append((locale, round(seconds)))
        # The first piece is Vietnamese, the second English.
        first = seconds < 125
        confident = (locale == "vi-VN") == first
        return FakeResponse(json.dumps({"phrases": [
            {"offsetMilliseconds": 500, "durationMilliseconds": 1000, "text": f"in {locale}", "locale": locale,
             "confidence": 0.8 if confident else 0.3}]}).encode())

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    t = azure(monkeypatch, tmp_path)
    t.encode_audio = lambda audio: b"X" * len(audio)
    audio = np.ones(SAMPLE_RATE * 250, np.float32)
    audio[SAMPLE_RATE * 112: SAMPLE_RATE * 113] = 0  # the quiet moment the pieces are cut at
    language, segs = t.transcribe(audio, 100.0)
    assert [s[2] for s in segs] == ["in vi-VN", "in en-US"]
    assert segs[0][0] == 100.5 and segs[1][0] == pytest.approx(100.0 + 112 + 0.5, abs=0.6)
    assert sorted(requests) == [("en-US", 112), ("en-US", 138), ("vi-VN", 112), ("vi-VN", 138)]


def test_azure_with_a_language_sends_one_request(monkeypatch, tmp_path):
    requests = []

    def urlopen(request, timeout):
        requests.append(sent_definition(request)["locales"])
        return FakeResponse(b'{"phrases": []}')

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    azure(monkeypatch, tmp_path, language="en").transcribe(np.zeros(SAMPLE_RATE * 600, np.float32), 0.0)
    assert requests == [["en-US"]]


def test_azure_keeps_the_main_language_unless_another_is_clearly_more_confident(monkeypatch, tmp_path):
    # Four pieces: three clearly Vietnamese, one where English is only a little more confident, one clearly English.
    confidences = {0: (0.3, 0.8), 1: (0.3, 0.8), 2: (0.55, 0.5), 3: (0.9, 0.2)}

    def urlopen(request, timeout):
        (locale,) = sent_definition(request)["locales"]
        piece = round(request.data.count(b"X") / SAMPLE_RATE / 100) - 1
        en, vi = confidences[piece]
        return FakeResponse(json.dumps({"phrases": [
            {"offsetMilliseconds": 0, "durationMilliseconds": 60000, "text": f"{piece} {locale}", "locale": locale,
             "confidence": en if locale == "en-US" else vi}]}).encode())

    monkeypatch.setattr(tr.urllib.request, "urlopen", urlopen)
    t = azure(monkeypatch, tmp_path)
    # Pieces of 100, 200, 300 and 400 s (cut at the quiet seconds), so a request's length says which piece it is.
    t.encode_audio = lambda audio: b"X" * len(audio)
    monkeypatch.setattr(tr, "split_points", lambda audio, seconds, search_seconds: [0, 100 * SAMPLE_RATE, 300 * SAMPLE_RATE,
                                                                                    600 * SAMPLE_RATE, 1000 * SAMPLE_RATE])
    language, segs = t.transcribe(np.ones(SAMPLE_RATE * 1000, np.float32), 0.0)
    assert [s[2] for s in segs] == ["0 vi-VN", "1 vi-VN", "2 vi-VN", "3 en-US"]
