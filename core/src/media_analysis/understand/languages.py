"""Which languages a recording's speech is in, measured from its transcript."""

from __future__ import annotations

from media_analysis.understand.models import Transcript

NAMES = {"en": "English", "vi": "Vietnamese", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French",
         "de": "German", "es": "Spanish", "th": "Thai", "id": "Indonesian"}
SHOWN_SHARE = 0.05
"""Smaller shares are left out: they are usually a few misrecognised words, not speech in that language."""
MAIN_SHARE = 0.2
"""A language with at least this share counts as one the meeting is held in."""


def language_shares(transcript: Transcript) -> dict[str, float]:
    """Share of the speech per language, largest first."""
    seconds: dict[str, float] = {}
    for ft in transcript.files:
        for lang, s in ft.speech_by_language().items():
            seconds[lang] = seconds.get(lang, 0.0) + s
    total = sum(seconds.values())
    if not total:
        return {}
    return {lang: round(s / total, 3) for lang, s in sorted(seconds.items(), key=lambda kv: -kv[1])}


def name(code: str) -> str:
    return NAMES.get(code, code)


def describe(shares: dict[str, float]) -> str:
    """E.g. "Vietnamese 85%, English 15%"; empty when nothing was transcribed."""
    return ", ".join(f"{name(lang)} {share:.0%}" for lang, share in shares.items() if share >= SHOWN_SHARE)


def main_languages(shares: dict[str, float]) -> list[str]:
    """The languages the meeting is held in, largest first (at least one when there was speech)."""
    main = [lang for lang, share in shares.items() if share >= MAIN_SHARE]
    return main or list(shares)[:1]


def transcript_note(shares: dict[str, float]) -> str | None:
    """What Claude should know about the transcript's languages; None when the speech is (almost) all English."""
    shown = {lang: share for lang, share in shares.items() if share >= SHOWN_SHARE}
    if not shown or list(shown) == ["en"]:
        return None
    main = name(next(iter(shown)))
    if main == "English":
        caution = "speech in the other languages is often transcribed poorly"
    else:
        caution = f"English technical terms and names inside {main} speech are often misheard"
    return (f"Speech: {describe(shares)} (detected). The transcript is automatic speech recognition: {caution}, so "
            "work out what was meant from the context.")
