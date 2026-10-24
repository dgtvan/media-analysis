import numpy as np

from media_analysis.providers.transcriber import SAMPLE_RATE, split_points


def test_split_points_cut_at_quiet_moments():
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.3, SAMPLE_RATE * 130).astype(np.float32)
    quiet = SAMPLE_RATE * 55
    audio[quiet: quiet + SAMPLE_RATE] = 0
    cuts = split_points(audio, chunk_seconds=60, search_seconds=10)
    assert cuts[0] == 0 and cuts[-1] == len(audio)
    assert quiet <= cuts[1] <= quiet + SAMPLE_RATE


def test_split_points_short_audio_is_one_chunk():
    audio = np.zeros(SAMPLE_RATE * 30, np.float32)
    assert split_points(audio, chunk_seconds=60) == [0, len(audio)]
