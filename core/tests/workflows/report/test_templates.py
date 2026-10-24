import pytest

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.workflows.report.templates.registry import load_templates, new_template, parse_template


def test_builtin_templates_load():
    templates = load_templates([])
    assert {"meeting", "technical-discussion", "lecture-tutorial", "interview", "language-practice",
            "casual-chat"} <= set(templates)
    assert all(t.sections for t in templates.values())


def test_generic_template_is_never_offered_to_the_classifier():
    templates = load_templates([])
    assert templates["generic"].fallback
    assert [t.id for t in templates.values() if t.fallback] == ["generic"]


def test_user_template_overrides_builtin(tmp_path):
    (tmp_path / "meeting.md").write_text(
        "---\nid: meeting\nname: My meeting\ndescription: d\nsections: [A]\n---\nbody", encoding="utf-8"
    )
    assert load_templates([tmp_path])["meeting"].name == "My meeting"


def test_invalid_template_is_reported():
    with pytest.raises(MediaAnalysisError):
        parse_template("no front matter", "x.md")
    with pytest.raises(MediaAnalysisError):
        parse_template("---\nid: Bad Id\nname: n\ndescription: d\nsections: [A]\n---\n", "x.md")


def test_new_template_skeleton_parses(tmp_path):
    path = new_template("sales-call", tmp_path)
    assert parse_template(path.read_text(encoding="utf-8"), str(path)).id == "sales-call"
    with pytest.raises(MediaAnalysisError):
        new_template("sales-call", tmp_path)
