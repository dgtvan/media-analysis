from fastapi.testclient import TestClient

from media_analysis.engine.service import EventHub, create_app
from media_analysis.engine.settings import Settings
from media_analysis.workflows.edit.api import EditService, mount_edit_api


def client(tmp_path):
    hub = EventHub()
    app = create_app("secret", hub)
    mount_edit_api(app, EditService(tmp_path / "projects", Settings, hub, []))
    return TestClient(app)


def test_projects_footage_and_token(tmp_path):
    c = client(tmp_path)
    h = {"X-Token": "secret"}
    assert c.get("/api/projects").status_code == 403
    assert c.get("/api/projects", headers={"X-Token": "wrong"}).status_code == 403
    assert "talking-head" in [p["id"] for p in c.get("/api/profiles", headers=h).json()]
    p = c.post("/api/projects", json={"name": "Hike day!", "profile_id": "travel-montage", "max_seconds": 45}, headers=h).json()
    assert p["id"].startswith("hike-day-") and p["max_seconds"] == 45 and p["files"] == 0
    assert c.post("/api/projects", json={"name": "x", "profile_id": "nope"}, headers=h).status_code == 400

    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    files = c.post(f"/api/projects/{p['id']}/footage", json={"paths": [str(tmp_path)]}, headers=h).json()
    assert [f["name"] for f in files] == ["a.mp4"]
    again = c.post(f"/api/projects/{p['id']}/footage", json={"paths": [str(clip)]}, headers=h).json()
    assert len(again) == 1

    changed = c.patch(f"/api/projects/{p['id']}", json={"clear_max_seconds": True, "profile_id": "talking-head"}, headers=h).json()
    assert changed["max_seconds"] is None and changed["profile_id"] == "talking-head"
    assert c.get(f"/api/projects/{p['id']}/edit", headers=h).status_code == 404
    assert c.post(f"/api/projects/{p['id']}/undo", headers=h).status_code == 409
    assert c.get("/api/projects/../etc", headers=h).status_code == 404
    assert c.delete(f"/api/projects/{p['id']}", headers=h).status_code == 200
    assert c.get("/api/projects", headers=h).json() == []


def test_imported_footage_gets_a_thumbnail_and_length_before_analysis(tmp_path):
    import subprocess

    import pytest

    from media_analysis.engine.errors import MediaAnalysisError
    from media_analysis.media import ffmpeg

    try:
        exe = ffmpeg.find_binary("ffmpeg")
    except MediaAnalysisError:
        pytest.skip("ffmpeg not found")
    clip = tmp_path / "clip.mp4"
    subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    c = client(tmp_path)
    h = {"X-Token": "secret"}
    p = c.post("/api/projects", json={"name": "t"}, headers=h).json()
    files = c.post(f"/api/projects/{p['id']}/footage", json={"paths": [str(clip)]}, headers=h).json()
    assert files[0]["duration"] == pytest.approx(3.0, abs=0.1) and files[0]["has_video"] is True
    r = c.get(files[0]["thumb"], headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 1000
    assert c.get(f"/media/{p['id']}/thumb/0123456789abcdef.jpg", headers=h).status_code == 404
