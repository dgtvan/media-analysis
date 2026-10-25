import pytest
from pydantic import BaseModel

from media_analysis.engine.documents import DocumentStore, diff_ids
from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.profiles import Profile, parse_profile, render_profile
from media_analysis.providers.agent import AgentTool, ToolError


class Doc(BaseModel):
    items: dict[str, dict]


def test_versions_keep_parents_and_undo_goes_back(tmp_path):
    store = DocumentStore(tmp_path, "edit", Doc)
    with pytest.raises(MediaAnalysisError):
        store.load()
    v1 = store.save(Doc(items={"c1": {"in": 0}}), author="analysis", summary="first")
    v2 = store.save(Doc(items={"c1": {"in": 1}}), author="chat", summary="trim")
    assert (v1.version, v1.parent, v2.version, v2.parent) == (1, None, 2, 1)
    assert store.load().items["c1"]["in"] == 1
    assert store.undo().version == 1 and store.load().items["c1"]["in"] == 0
    v3 = store.save(Doc(items={"c2": {}}), author="chat", summary="branch")
    assert v3.parent == 1 and [v.version for v in store.versions()] == [1, 2, 3]
    assert store.make_current(2).version == 2 and store.load(3).items == {"c2": {}}
    with pytest.raises(MediaAnalysisError):
        DocumentStore(tmp_path, "other", Doc).undo()


def test_diff_ids():
    assert diff_ids({"a": {"x": 1}, "b": {}}, {"a": {"x": 2}, "c": {}}) == {"added": ["c"], "removed": ["b"], "changed": ["a"]}


class Style(Profile):
    pace: str = "medium"


def test_profiles_have_settings_and_guidance():
    text = render_profile({"id": "travel", "name": "Travel", "description": "d", "pace": "fast"}, "Keep it moving.")
    p = parse_profile(text, "x.md", Style)
    assert (p.pace, p.instructions) == ("fast", "Keep it moving.")
    assert "source" not in p.fingerprint_parts()


class Trim(BaseModel):
    clip: str
    seconds: float


def test_tool_arguments_are_validated_and_errors_go_back_to_claude():
    def trim(a: Trim) -> str:
        if a.clip != "#1":
            raise ToolError(f"There is no clip {a.clip}.")
        return f"Trimmed {a.clip} by {a.seconds}s."

    t = AgentTool("trim", "Trims a clip.", Trim, trim)
    assert t.call({"clip": "#1", "seconds": 2}) == ("Trimmed #1 by 2.0s.", False)
    assert t.call({"clip": "#9", "seconds": 2}) == ("There is no clip #9.", True)
    text, is_error = t.call({"clip": "#1"})
    assert is_error and "seconds" in text
