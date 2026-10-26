"""Speech-to-text: faster-whisper on this computer (CUDA when available, CPU otherwise) or Azure AI Speech fast
transcription. Both take chunks of 16 kHz mono audio and return the same segments."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

import numpy as np

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.hardware import Hardware, detect
from media_analysis.engine.settings import AZURE_KEY_ENV, USER_MODELS_DIR, TranscriptionSettings, read_terms

SAMPLE_RATE = 16000
# Whisper tidies away fillers unless the text it continues from has them; starting from such text keeps "um" and "uh",
# which editing needs to see in order to cut them.
VERBATIM_PROMPT = "Umm, let me think, like, hmm... Okay, so, uh, I mean, you know, the thing is..."


Segment = tuple[float, float, str, list[tuple[float, float, str]] | None, str | None]
"""(start, end, text, words, language): times in seconds; words are (start, end, word), or None when word times are
off; language is an ISO code like "vi", or None when unknown."""


class Transcriber(Protocol):
    description: str
    """What does the work, for the log, e.g. "model large-v3 on cpu (int8)"."""
    config: dict
    """The settings that shape its transcripts, for the records of how an analysis was made."""

    def transcribe(
        self, audio: np.ndarray, offset: float, on_progress: Callable[[float], None] | None = None,
    ) -> tuple[str | None, list[Segment]]:
        """Transcribe one chunk into its language and segments; times are shifted by `offset` seconds. `on_progress`
        gets the seconds of the chunk done so far."""
        ...


def create_transcriber(settings: TranscriptionSettings, encode_audio: Callable[[np.ndarray], bytes]) -> Transcriber:
    """The transcriber the settings choose. `encode_audio` turns a chunk into a FLAC file for uploading."""
    if settings.engine == "azure-speech":
        return AzureSpeechTranscriber(settings, encode_audio)
    return FasterWhisperTranscriber(settings, detect())


class FasterWhisperTranscriber:
    def __init__(self, settings: TranscriptionSettings, hardware: Hardware) -> None:
        self.settings = settings
        self.device = hardware.device if settings.device == "auto" else settings.device
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self.cpu_threads = hardware.cpu_threads
        self.description = f"model {settings.model} on {self.device} ({self.compute_type})"
        self.config = {
            "engine": "local", "model": settings.model, "device": self.device, "compute_type": self.compute_type,
            "beam_size": settings.beam_size, "language": settings.language or "detected every 30 s",
            "chunk_minutes": settings.chunk_minutes, "word_timestamps": settings.word_timestamps,
            "verbatim": settings.verbatim,
        }
        self._model = None

    def _load(self):
        if self._model is None:
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from faster_whisper import WhisperModel
            from huggingface_hub.utils import logging as hf_logging

            hf_logging.set_verbosity_error()
            kwargs = dict(
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads if self.device == "cpu" else 0,
            )
            # Use a local copy (shared first, then the user's own) without touching the network. Otherwise download
            # into the shared folder so every account gets it, or into the user's cache if that folder isn't writable.
            shared = str(self.settings.models_dir) if self.settings.models_dir else None
            own = {"download_root": str(USER_MODELS_DIR)} if USER_MODELS_DIR else {}
            attempts = [{"download_root": shared, "local_files_only": True}] if shared else []
            attempts += [{**own, "local_files_only": True}]
            attempts += [{"download_root": shared}] if shared else []
            attempts += [own]
            for i, extra in enumerate(attempts):
                try:
                    self._model = WhisperModel(self.settings.model, **extra, **kwargs)
                    break
                except Exception:
                    if i == len(attempts) - 1:
                        raise
        return self._model

    def transcribe(
        self,
        audio: np.ndarray,
        offset: float,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[str | None, list[Segment]]:
        model = self._load()
        segments, info = model.transcribe(
            audio,
            language=self.settings.language,
            # Re-detects the language every 30 s window, so EN/VI code-switching keeps working.
            multilingual=self.settings.language is None,
            beam_size=self.settings.beam_size,
            vad_filter=True,
            # Feeding previous text back in tends to cause repetition loops on long or mixed-language audio.
            condition_on_previous_text=False,
            word_timestamps=self.settings.word_timestamps,
            initial_prompt=VERBATIM_PROMPT if self.settings.verbatim else None,
        )
        out: list[Segment] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                words = None
                if self.settings.word_timestamps and seg.words:
                    words = [(offset + w.start, offset + w.end, w.word.strip()) for w in seg.words if w.word.strip()]
                # faster-whisper reports the language detected at the start of the chunk, not per segment.
                out.append((offset + seg.start, offset + seg.end, text, words, info.language))
            if on_progress:
                on_progress(seg.end)
        return info.language, out


AZURE_API_VERSION = "2025-10-15"
# Without a language setting, speech is transcribed in each of these and the more confident answer kept.
AZURE_DETECT_LOCALES = ["en-US", "vi-VN"]
AZURE_LOCALES = {"en": "en-US", "vi": "vi-VN"}
AZURE_MAX_TERMS = 500
AZURE_DETECT_PIECE_SECONDS = 120
AZURE_PARALLEL_REQUESTS = 4
AZURE_SWITCH_MARGIN = 0.1


def azure_url(endpoint: str) -> str:
    """The fast transcription URL of an Azure AI Speech or AI Services resource endpoint."""
    parts = urllib.parse.urlsplit(endpoint.strip())
    if parts.scheme != "https" or not parts.netloc:
        raise MediaAnalysisError(
            f"The Azure endpoint must be an https address, such as https://<resource>.services.ai.azure.com/ (got '{endpoint}').")
    return f"https://{parts.netloc}/speechtotext/transcriptions:transcribe?api-version={AZURE_API_VERSION}"


class AzureSpeechTranscriber:
    """Azure AI Speech fast transcription. Each chunk is one request; rate limits and brief outages are waited out."""

    attempts = 6
    timeout = 600

    def __init__(self, settings: TranscriptionSettings, encode_audio: Callable[[np.ndarray], bytes]) -> None:
        if not settings.azure_endpoint:
            raise MediaAnalysisError("Transcription is set to Azure AI Speech but no Azure endpoint is set.")
        self.key = os.environ.get(AZURE_KEY_ENV, "").strip()
        if not self.key:
            raise MediaAnalysisError(f"Transcription is set to Azure AI Speech but no Azure key is set ({AZURE_KEY_ENV}).")
        self.settings = settings
        self.encode_audio = encode_audio
        self.url = azure_url(settings.azure_endpoint)
        lang = settings.language
        self.locales = [AZURE_LOCALES.get(lang, lang)] if lang else AZURE_DETECT_LOCALES
        self.terms = read_terms(settings.terms_file)[:AZURE_MAX_TERMS]
        terms = f", {len(self.terms)} terms" if self.terms else ""
        self.description = f"Azure AI Speech at {urllib.parse.urlsplit(self.url).netloc} ({', '.join(self.locales)}{terms})"
        self.config = {
            "engine": "azure-speech", "endpoint": urllib.parse.urlsplit(self.url).netloc, "api_version": AZURE_API_VERSION,
            "languages": self.locales,
            "language_choice": "fixed" if len(self.locales) == 1
            else f"each {AZURE_DETECT_PIECE_SECONDS // 60}-minute piece in every language; the chunk's main language "
            f"kept unless another is more confident by over {AZURE_SWITCH_MARGIN}",
            "terms": len(self.terms), "chunk_minutes": settings.chunk_minutes, "word_timestamps": settings.word_timestamps,
        }

    def transcribe(
        self,
        audio: np.ndarray,
        offset: float,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[str | None, list[Segment]]:
        parts = self._parts(audio, offset)
        if on_progress:
            on_progress(len(audio) / SAMPLE_RATE)
        segments = [seg for result, part_offset in parts
                    for seg in _segments(result, part_offset, self.settings.word_timestamps)]
        return _language({"phrases": [p for result, _ in parts for p in result.get("phrases") or []]}), segments

    def _parts(self, audio: np.ndarray, offset: float) -> list[tuple[dict, float]]:
        """Responses for `audio` with the offset each one starts at. With one language set it is one request.
        Otherwise each ~2 minute piece is transcribed in every candidate language and the answer Azure is more
        confident in is kept: Azure's own language detection picked English for mostly Vietnamese meetings."""
        if len(self.locales) == 1:
            return [(self._request(audio, self.locales), offset)]
        cuts = split_points(audio, AZURE_DETECT_PIECE_SECONDS, search_seconds=10)
        jobs = [(a, b, locale) for a, b in zip(cuts[:-1], cuts[1:]) for locale in self.locales]
        with ThreadPoolExecutor(AZURE_PARALLEL_REQUESTS) as pool:
            answers = list(pool.map(lambda job: self._request(audio[job[0]:job[1]], [job[2]]), jobs))
        n = len(self.locales)
        pieces = [dict(zip(self.locales, answers[i * n:(i + 1) * n])) for i in range(len(cuts) - 1)]
        # The language most of the chunk is in wins a piece unless another is clearly more confident there, so a
        # short or quiet piece doesn't flip to the wrong language.
        won: dict[str, float] = {}
        for piece in pieces:
            best = max(piece, key=lambda loc: _confidence(piece[loc]))
            won[best] = won.get(best, 0.0) + _speech_seconds(piece[best])
        main = max(won, key=won.__getitem__) if any(won.values()) else self.locales[0]

        def choose(piece: dict[str, dict]) -> dict:
            best = max(piece, key=lambda loc: _confidence(piece[loc]))
            switch = _confidence(piece[best]) > _confidence(piece[main]) + AZURE_SWITCH_MARGIN
            return piece[best] if switch else piece[main]

        return [(choose(piece), offset + a / SAMPLE_RATE) for piece, a in zip(pieces, cuts[:-1])]

    def _request(self, audio: np.ndarray, locales: list[str]) -> dict:
        definition: dict = {"locales": locales, "profanityFilterMode": "None"}
        if self.terms:
            definition["phraseList"] = {"phrases": self.terms}
        return self._post(definition, self.encode_audio(audio))

    def _post(self, definition: dict, flac: bytes) -> dict:
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="definition"\r\n'
                f"Content-Type: application/json\r\n\r\n{json.dumps(definition)}\r\n"
                f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="audio.flac"\r\n'
                f"Content-Type: audio/flac\r\n\r\n").encode() + flac + f"\r\n--{boundary}--\r\n".encode()
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": f"multipart/form-data; boundary={boundary}"}
        for attempt in range(1, self.attempts + 1):
            request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = _error_detail(e)
                # Language detection gives up on a chunk without speech (silence, music) instead of returning nothing.
                if e.code == 422 and "no language was identified" in detail.lower():
                    return {"phrases": []}
                if e.code in (429, 500, 502, 503, 504) and attempt < self.attempts:
                    time.sleep(_retry_after(e, attempt))
                    continue
                raise MediaAnalysisError(_explain(e.code, detail)) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < self.attempts:
                    time.sleep(_retry_after(None, attempt))
                    continue
                reason = getattr(e, "reason", e)
                raise MediaAnalysisError(f"Azure AI Speech could not be reached ({reason}). Check the endpoint and the "
                                         "internet connection.") from None
        raise AssertionError("unreachable")


def _confidence(result: dict) -> float:
    """Azure's confidence in a response, averaged over its phrases by length; 0 without speech."""
    phrases = [p for p in result.get("phrases") or [] if p.get("confidence") is not None]
    total = sum(float(p.get("durationMilliseconds", 0)) for p in phrases)
    return sum(float(p["confidence"]) * float(p.get("durationMilliseconds", 0)) for p in phrases) / total if total else 0.0


def _speech_seconds(result: dict) -> float:
    return sum(float(p.get("durationMilliseconds", 0)) for p in result.get("phrases") or []) / 1000


def _code(locale: str | None) -> str | None:
    return str(locale).split("-")[0].lower() if locale else None


def _language(result: dict) -> str | None:
    """The language most of the speech is in, as an ISO code like faster-whisper's ("vi" for vi-VN)."""
    seconds: dict[str, float] = {}
    for p in result.get("phrases") or []:
        if code := _code(p.get("locale")):
            seconds[code] = seconds.get(code, 0.0) + float(p.get("durationMilliseconds", 0))
    return max(seconds, key=seconds.__getitem__) if seconds else None


def _segments(result: dict, offset: float, with_words: bool) -> list[Segment]:
    """Phrases of a fast transcription response as segments, shifted by `offset` seconds."""
    out: list[Segment] = []
    for p in result.get("phrases") or []:
        text = str(p.get("text", "")).strip()
        if not text:
            continue
        start = offset + float(p["offsetMilliseconds"]) / 1000
        end = start + float(p["durationMilliseconds"]) / 1000
        words = None
        if with_words and p.get("words"):
            words = []
            for w in p["words"]:
                ws = offset + float(w["offsetMilliseconds"]) / 1000
                if word := str(w.get("text", "")).strip():
                    words.append((ws, ws + float(w["durationMilliseconds"]) / 1000, word))
        out.append((start, end, text, words, _code(p.get("locale"))))
    return out


def _error_detail(e: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(e.read().decode("utf-8", "replace"))
        error = data.get("error", data)
        return str(error.get("message") or error.get("code") or "") if isinstance(error, dict) else str(error)
    except (ValueError, OSError):
        return ""


def _retry_after(e: urllib.error.HTTPError | None, attempt: int) -> float:
    if e is not None:
        for header, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            try:
                return min(float(e.headers.get(header)) * scale, 120.0)
            except (TypeError, ValueError):
                pass
    return min(5.0 * 2 ** (attempt - 1), 60.0)


def _explain(status: int, detail: str) -> str:
    if status == 403 and "firewall" in detail.lower():
        return ("Azure's firewall refused this computer. In the Azure portal, open the Speech resource, then Networking, "
                "tick 'Add your client IP address' and Save; it can take a few minutes to apply. Use that box rather "
                "than a what's-my-IP website: Azure may see a different address.")
    reason = {
        401: "Azure rejected the key. Check the Azure key in the settings (Keys and Endpoint of the Speech resource).",
        403: "Azure refused access with this key. Check the Azure key and the resource's network settings.",
        404: "Azure has no fast transcription at this address. Use the resource's AI services endpoint (Keys and "
             "Endpoint, AI Services tab), such as https://<resource>.services.ai.azure.com/, not a regional speech one.",
        413: "The audio chunk is too large for Azure. Use shorter chunks.",
        429: "Azure kept refusing with 'too many requests'. Wait a few minutes and analyse again.",
    }.get(status, f"Azure AI Speech failed (HTTP {status}).")
    return f"{reason}{f' Azure said: {detail}' if detail else ''}"


def check_transcriber(settings: TranscriptionSettings, encode_audio: Callable[[np.ndarray], bytes]) -> str:
    """Transcribes two seconds of quiet noise to confirm the engine works; returns what it used. Raises
    MediaAnalysisError with a reason when it doesn't."""
    transcriber = create_transcriber(settings, encode_audio)
    if isinstance(transcriber, AzureSpeechTranscriber):
        transcriber.attempts = 1  # someone is waiting for the answer
    noise = np.random.default_rng(0).normal(0, 0.001, SAMPLE_RATE * 2).astype(np.float32)
    transcriber.transcribe(noise, 0.0)
    return transcriber.description


def split_points(audio: np.ndarray, chunk_seconds: float, search_seconds: float = 20.0) -> list[int]:
    """Sample indices to cut long audio into chunks, nudged to the quietest moment near each target so cuts
    rarely fall mid-word. Returns boundaries including 0 and len(audio)."""
    total = len(audio)
    chunk = int(chunk_seconds * SAMPLE_RATE)
    if total <= chunk * 1.2:
        return [0, total]
    win = SAMPLE_RATE // 10
    n_windows = total // win
    energy = np.sqrt(np.mean(audio[: n_windows * win].reshape(n_windows, win) ** 2, axis=1))
    search = int(search_seconds * 10)
    cuts = [0]
    target = chunk
    while target < total - chunk * 0.2:
        centre = target // win
        lo, hi = max(centre - search, 1), min(centre + search, n_windows - 1)
        best = lo + int(np.argmin(energy[lo:hi])) if hi > lo else centre
        cut = best * win
        if cut > cuts[-1]:
            cuts.append(cut)
        target = cut + chunk
    cuts.append(total)
    return cuts
