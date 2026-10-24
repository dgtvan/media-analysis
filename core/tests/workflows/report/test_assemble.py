from media_analysis.understand.models import Ingest, MediaFile
from media_analysis.workflows.report.assemble import assemble_output
from media_analysis.workflows.report.models import (
    Classification, Evidence, EvidenceItem, ReportDraft, SuggestedTemplate,
)
from media_analysis.workflows.report.templates.registry import load_templates, parse_template


def media(name, video=False):
    return MediaFile(id="f1", path=name, name=name, fingerprint="fp", duration=90, has_video=video, has_audio=True)


def test_assemble_links_only_referenced_frames(tmp_path):
    frame = tmp_path / "c1.jpg"
    frame.write_bytes(b"jpg")
    unused = tmp_path / "c2.jpg"
    unused.write_bytes(b"jpg")
    ingest = Ingest(files=[media("My Video.mp4", video=True)])
    evidence = Evidence(items=[
        EvidenceItem(id="E01", file_id="f1", time=75, path=str(frame), caption="Diagram", description="d", text="A -> B"),
        EvidenceItem(id="E02", file_id="f1", time=80, path=str(unused), caption="Other", description="d", text=""),
    ])
    draft = ReportDraft(title="T", markdown="# T\n\n## Summary\n![Arch](evidence:E01)\n![x](evidence:E99)\n")
    template = load_templates([])["meeting"]
    out = tmp_path / "out"
    out.mkdir()
    report, warnings = assemble_output(out, draft, evidence, ingest, template, Classification(
        template_id="meeting", confidence=0.9, reason="r"))
    text = report.read_text(encoding="utf-8")
    assert "![Arch](reference/frames/My-Video_00-01-15.jpg)" in text
    assert "evidence:" not in text
    assert (out / "reference/text/My-Video_00-01-15.md").read_text(encoding="utf-8").count("A -> B") == 1
    assert sorted(p.name for p in (out / "reference/frames").iterdir()) == ["My-Video_00-01-15.jpg"]
    assert any("E99" in w for w in warnings)


def test_generic_report_explains_the_fit_and_writes_a_usable_template(tmp_path):
    ingest = Ingest(files=[media("talk.mp3")])
    classification = Classification(
        template_id="generic", confidence=0.2, reason="It is a cooking show.", closest=["lecture-tutorial"],
        mismatch=["No teaching structure"], fallback=True,
        suggested=SuggestedTemplate(id="cooking-show", name="Cooking show", description="A recipe demo.",
                                    signals=["ingredients"], evidence=["the dish"], sections=["Recipe", "Steps"]),
    )
    out = tmp_path / "out"
    out.mkdir()
    report, _ = assemble_output(out, ReportDraft(title="T", markdown="# T\n\n## Summary\nx"), Evidence(items=[]),
                                ingest, load_templates([])["generic"], classification)
    text = report.read_text(encoding="utf-8")
    assert "## Template fit" in text and "No teaching structure" in text and "`lecture-tutorial`" in text
    suggested = parse_template((out / "reference/suggested-template.md").read_text(encoding="utf-8"), "suggested")
    assert suggested.id == "cooking-show" and suggested.sections == ["Recipe", "Steps"]
