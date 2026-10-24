from pydantic import BaseModel

from media_analysis.engine.cache import Cache, digest
from media_analysis.engine.timecode import format_timecode


class Result(BaseModel):
    title: str


def test_format_timecode():
    assert format_timecode(3725.9) == "01:02:05"
    assert format_timecode(65, "-") == "00-01-05"


def test_digest_is_stable_and_order_independent():
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    result = Result(title="t")
    cache.save("report", "k", result)
    assert cache.load("report", "k", Result) == result
    assert cache.load("report", "missing", Result) is None
