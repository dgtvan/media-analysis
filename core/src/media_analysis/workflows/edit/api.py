"""The Project API for the Footage Editor's presentation layer (served by engine/service.py).

Everything the UI shows comes from here: projects and their footage, the profiles, analysis progress (as events),
the edit versions in the shape the timeline draws (seconds), the chat. Media files (proxies, filmstrips) are served
from the project folder. The UI never reads files itself.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.service import EventHub, Job
from media_analysis.media import ffmpeg
from media_analysis.understand.ingest import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from media_analysis.workflows.edit.chat import EditChat
from media_analysis.workflows.edit.export import edit_json, write_exports
from media_analysis.workflows.edit.profiles.registry import load_edit_profiles
from media_analysis.workflows.edit.workflow import (
    PROJECT_FILE, USER_EDIT_PROFILES_DIR, ProjectInfo, edit_store, load_project_info, open_project, proxy_path,
    save_project, strip_every, strip_path,
)

API_VERSION = 1


class NewProject(BaseModel):
    name: str
    profile_id: str = "generic"
    max_seconds: int | None = None


class ProjectChange(BaseModel):
    name: str | None = None
    profile_id: str | None = None
    max_seconds: int | None = None
    clear_max_seconds: bool = False


class AddFiles(BaseModel):
    paths: list[str]


class ChatMessage(BaseModel):
    message: str


class EditService:
    def __init__(self, projects_root: Path, settings_factory, hub: EventHub, cli_args: list[str]) -> None:
        self.root = projects_root
        self.settings_factory = settings_factory
        self.hub = hub
        self.cli_args = cli_args
        """Extra CLI options for analysis runs (Claude profile, ...)."""
        self.jobs: dict[str, Job] = {}
        self.chatting: set[str] = set()
        self._open: dict[str, tuple[Any, Any, str]] = {}
        self._probes: dict[str, dict | None] = {}
        self._lock = threading.Lock()

    # --- projects -------------------------------------------------------------------------------------------------

    def dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", project_id) or not (self.root / project_id / PROJECT_FILE).exists():
            raise HTTPException(404, f"No project {project_id}.")
        return self.root / project_id

    def summary(self, project_id: str) -> dict:
        d = self.root / project_id
        info = load_project_info(d)
        store = edit_store(d)
        job = self.jobs.get(project_id)
        versions = store.versions()
        return {
            "id": project_id, "name": info.name, "profile_id": info.profile_id, "max_seconds": info.max_seconds,
            "files": len(info.files or []), "analysing": bool(job and job.running),
            "chatting": project_id in self.chatting, "current_version": store.current(), "versions": len(versions),
            "updated": versions[-1].created if versions else None,
        }

    def create(self, body: NewProject) -> dict:
        self._check_profile(body.profile_id)
        slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:40] or "project"
        project_id = f"{slug}-{secrets.token_hex(3)}"
        save_project(self.root / project_id, ProjectInfo(
            profile_id=body.profile_id, max_seconds=body.max_seconds, files=[], name=body.name.strip() or "Untitled",
        ))
        return self.summary(project_id)

    def _check_profile(self, profile_id: str) -> None:
        if profile_id not in load_edit_profiles([USER_EDIT_PROFILES_DIR]):
            raise HTTPException(400, f"Unknown profile {profile_id}.")

    def change(self, project_id: str, body: ProjectChange) -> dict:
        d = self.dir(project_id)
        info = load_project_info(d)
        if body.name is not None:
            info.name = body.name.strip() or info.name
        if body.profile_id is not None:
            self._check_profile(body.profile_id)
            info.profile_id = body.profile_id
        if body.max_seconds is not None:
            info.max_seconds = body.max_seconds
        if body.clear_max_seconds:
            info.max_seconds = None
        save_project(d, info)
        self._open.pop(project_id, None)
        return self.summary(project_id)

    def delete(self, project_id: str) -> None:
        d = self.dir(project_id)
        self.stop(project_id)
        shutil.rmtree(d, ignore_errors=True)

    def footage(self, project_id: str) -> list[dict]:
        """The imported files, with their length and a thumbnail link as soon as they are imported (no analysis)."""
        d = self.dir(project_id)
        out = []
        for i, path in enumerate(load_project_info(d).files or []):
            p = Path(path)
            item = {"index": i, "path": path, "name": p.name, "exists": p.is_file(), "duration": None, "has_video": None,
                    "thumb": None}
            if p.is_file():
                info = self._probe(p)
                if info:
                    item.update(duration=info["duration"], has_video=info["has_video"])
                    if info["has_video"]:
                        item["thumb"] = f"/media/{project_id}/thumb/{_file_key(p)}.jpg"
            out.append(item)
        return out

    def _probe(self, path: Path) -> dict | None:
        key = _file_key(path)
        if key not in self._probes:
            try:
                self._probes[key] = ffmpeg.probe(path)
            except MediaAnalysisError:
                self._probes[key] = None
        return self._probes[key]

    def thumbnail(self, project_id: str, key: str) -> Path:
        """A frame from early in the clip (not the very first, which is often black), made once per file version."""
        d = self.dir(project_id)
        out = d / "media" / "thumbs" / f"{key}.jpg"
        if out.exists():
            return out
        for path in load_project_info(d).files or []:
            p = Path(path)
            if p.is_file() and _file_key(p) == key:
                info = self._probe(p)
                if not info or not info["has_video"]:
                    break
                at = min(1.0, info["duration"] * 0.1)
                try:
                    return ffmpeg.extract_still(p, at, out, max_side=320)
                except MediaAnalysisError:
                    break
        raise HTTPException(404, "No thumbnail.")

    def add_files(self, project_id: str, body: AddFiles) -> list[dict]:
        d = self.dir(project_id)
        info = load_project_info(d)
        files = list(info.files or [])
        for raw in body.paths:
            p = Path(raw)
            if p.is_dir():
                candidates = sorted(x for x in p.iterdir() if x.is_file())
            else:
                candidates = [p]
            for c in candidates:
                if c.suffix.lower() in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS and c.is_file() and str(c) not in files:
                    files.append(str(c))
        info.files = files
        save_project(d, info)
        return self.footage(project_id)

    def remove_file(self, project_id: str, index: int) -> list[dict]:
        d = self.dir(project_id)
        info = load_project_info(d)
        if not 0 <= index < len(info.files or []):
            raise HTTPException(404, "No such file.")
        info.files = [f for i, f in enumerate(info.files or []) if i != index]
        save_project(d, info)
        return self.footage(project_id)

    # --- analysis -------------------------------------------------------------------------------------------------

    def analyse(self, project_id: str, fresh: bool = False) -> dict:
        d = self.dir(project_id)
        job = self.jobs.get(project_id)
        if job and job.running:
            raise HTTPException(409, "Already analysing.")
        if not load_project_info(d).files:
            raise HTTPException(400, "Import some footage first.")
        args = ["edit-project", str(d), "--json", *self.cli_args, *(["--fresh"] if fresh else [])]

        def done(code: int) -> None:
            self._open.pop(project_id, None)
            self.hub.publish(project_id, {"source": "project", "event": "project_changed"})

        self.jobs[project_id] = Job(project_id, args, self.hub, on_exit=done)
        return self.summary(project_id)

    def stop(self, project_id: str) -> None:
        job = self.jobs.get(project_id)
        if job:
            job.stop()

    def status(self, project_id: str) -> dict:
        job = self.jobs.get(project_id)
        return {"analysing": bool(job and job.running), "last": job.last if job else None}

    # --- the edit -------------------------------------------------------------------------------------------------

    def sources(self, project_id: str) -> list[dict]:
        """Every footage file with its id in the edit, length and media links (after an analysis)."""
        d = self.dir(project_id)
        u = self._understanding(project_id)[0]
        out = []
        for f in u.ingest.files:
            proxy = proxy_path(d, f.fingerprint)
            out.append({
                "id": f.id, "name": f.name, "path": f.path, "duration": f.duration, "has_video": f.has_video,
                "has_audio": f.has_audio,
                "proxy": f"/media/{project_id}/{proxy.name}" if proxy.exists() else None,
                "strip": f"/media/{project_id}/{strip_path(d, f.fingerprint).name}" if strip_path(d, f.fingerprint).exists() else None,
                "strip_every": strip_every(f.duration),
            })
        return out

    def edit(self, project_id: str, version: int | None = None) -> dict:
        d = self.dir(project_id)
        store = edit_store(d)
        if store.current() is None:
            raise HTTPException(404, "No edit yet: analyse the footage first.")
        number = version or store.current()
        plan = store.load(number)
        info = next(v for v in store.versions() if v.version == number)
        changed: list[str] = []
        if info.parent is not None:
            before = store.load(info.parent).items()
            after = plan.items()
            changed = [i for i in after if i not in before or _content(after[i]) != _content(before[i])]
        u = self._understanding(project_id)[0]
        data = edit_json(plan, u.ingest, number)
        ids = {item["label"]: i for i, item in plan.items().items() if "label" in item}
        for tr in data["tracks"]:
            for item in tr["items"]:
                item["id"] = ids.get(item["label"])
                item["changed"] = item["id"] in changed
        return {"version": info.model_dump(), "edit": data, "sources": self.sources(project_id)}

    def versions(self, project_id: str) -> dict:
        store = edit_store(self.dir(project_id))
        return {"current": store.current(), "versions": [v.model_dump() for v in store.versions()]}

    def make_current(self, project_id: str, version: int) -> dict:
        d = self.dir(project_id)
        try:
            edit_store(d).make_current(version)
        except MediaAnalysisError as e:
            raise HTTPException(404, str(e)) from None
        self._export(project_id)
        self.hub.publish(project_id, {"source": "project", "event": "edit_changed", "version": version})
        return self.versions(project_id)

    def undo(self, project_id: str) -> dict:
        d = self.dir(project_id)
        try:
            info = edit_store(d).undo()
        except MediaAnalysisError as e:
            raise HTTPException(409, str(e)) from None
        self._export(project_id)
        self.hub.publish(project_id, {"source": "project", "event": "edit_changed", "version": info.version})
        return self.versions(project_id)

    def _export(self, project_id: str) -> None:
        d = self.dir(project_id)
        store = edit_store(d)
        write_exports(store.load(), self._understanding(project_id)[0].ingest, store.current(), d / "export")

    # --- chat -----------------------------------------------------------------------------------------------------

    def _understanding(self, project_id: str):
        with self._lock:
            if project_id not in self._open:
                d = self.dir(project_id)
                try:
                    u, profile = open_project(d, self.settings_factory())
                except MediaAnalysisError as e:
                    raise HTTPException(409, f"The footage isn't analysed yet: {e}") from None
                descriptions = {f"{x.file_id}#{x.index}": x.caption for x in (u.descriptions.shots if u.descriptions else [])}
                self._open[project_id] = (u, profile, descriptions)
            return self._open[project_id]

    def chat_history(self, project_id: str) -> list[dict]:
        d = self.dir(project_id)
        u, profile, descriptions = self._understanding(project_id)
        return EditChat(d, u.footage, u.view, profile, descriptions).history()

    def chat(self, project_id: str, body: ChatMessage) -> dict:
        d = self.dir(project_id)
        if project_id in self.chatting:
            raise HTTPException(409, "Claude is still answering the previous message.")
        job = self.jobs.get(project_id)
        if job and job.running:
            raise HTTPException(409, "Wait for the analysis to finish.")
        u, profile, descriptions = self._understanding(project_id)
        session = EditChat(d, u.footage, u.view, profile, descriptions)
        settings = self.settings_factory()
        self.chatting.add(project_id)

        def run() -> None:
            def emit(e) -> None:
                self.hub.publish(project_id, {"source": "chat", "event": e.kind, **e.data})

            try:
                answer, info = session.send(body.message, settings.llm, emit)
                if info:
                    write_exports(session.store.load(), u.ingest, info.version, d / "export")
                    self.hub.publish(project_id, {"source": "project", "event": "edit_changed", "version": info.version})
                self.hub.publish(project_id, {"source": "chat", "event": "chat.done", "text": answer,
                                              "version": info.version if info else None})
            except Exception as e:  # noqa: BLE001 - reported to the UI, the service keeps running
                self.hub.publish(project_id, {"source": "chat", "event": "chat.error", "message": str(e)})
            finally:
                self.chatting.discard(project_id)

        threading.Thread(target=run, daemon=True).start()
        return {"accepted": True, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _file_key(path: Path) -> str:
    """Identifies a file version cheaply (path, size, modification time) for thumbnails and probe results."""
    st = path.stat()
    return hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]


def _content(item: dict) -> dict:
    """An item without its position: moving because an earlier clip changed isn't a change to the item."""
    return {k: v for k, v in item.items() if k not in ("at", "offset")}


def mount_edit_api(app: FastAPI, svc: EditService) -> None:
    r = APIRouter(prefix="/api")

    @r.get("/info")
    def info() -> dict:
        return {"api_version": API_VERSION, "projects_root": str(svc.root)}

    @r.get("/busy")
    def busy() -> dict:
        """Whether an analysis or a chat turn is running (the app refuses to close for an install then)."""
        running = [pid for pid, job in svc.jobs.items() if job.running] + sorted(svc.chatting)
        return {"busy": bool(running), "projects": running}

    @r.get("/profiles")
    def profiles() -> list[dict]:
        return [
            {"id": p.id, "name": p.name, "description": p.description, "target_seconds": p.target_seconds,
             "max_seconds": p.max_seconds, "aspect": p.aspect}
            for p in load_edit_profiles([USER_EDIT_PROFILES_DIR]).values()
        ]

    @r.get("/projects")
    def projects() -> list[dict]:
        svc.root.mkdir(parents=True, exist_ok=True)
        ids = [p.name for p in svc.root.iterdir() if (p / PROJECT_FILE).exists()]
        return sorted((svc.summary(i) for i in ids), key=lambda s: s.get("updated") or "", reverse=True)

    r.post("/projects")(svc.create)
    r.get("/projects/{project_id}")(svc.summary)
    r.patch("/projects/{project_id}")(svc.change)
    r.delete("/projects/{project_id}")(svc.delete)
    r.get("/projects/{project_id}/footage")(svc.footage)
    r.post("/projects/{project_id}/footage")(svc.add_files)
    r.delete("/projects/{project_id}/footage/{index}")(svc.remove_file)
    r.post("/projects/{project_id}/analyse")(svc.analyse)
    r.post("/projects/{project_id}/stop")(svc.stop)
    r.get("/projects/{project_id}/status")(svc.status)
    r.get("/projects/{project_id}/edit")(svc.edit)
    r.get("/projects/{project_id}/versions")(svc.versions)
    r.post("/projects/{project_id}/versions/{version}/current")(svc.make_current)
    r.post("/projects/{project_id}/undo")(svc.undo)
    r.get("/projects/{project_id}/chat")(svc.chat_history)
    r.post("/projects/{project_id}/chat")(svc.chat)

    @r.get("/projects/{project_id}/export/edit.json")
    def export(project_id: str) -> FileResponse:
        path = svc.dir(project_id) / "export" / "edit.json"
        if not path.exists():
            raise HTTPException(404, "No edit yet.")
        return FileResponse(path, media_type="application/json", filename="edit.json")

    app.include_router(r)

    @app.get("/media/{project_id}/thumb/{key}.jpg")
    def thumb(project_id: str, key: str) -> FileResponse:
        if not re.fullmatch(r"[0-9a-f]{16}", key):
            raise HTTPException(404, "No thumbnail.")
        return FileResponse(svc.thumbnail(project_id, key), headers={"Cache-Control": "max-age=86400"})

    @app.get("/media/{project_id}/{name}")
    def media(project_id: str, name: str) -> FileResponse:
        if not re.fullmatch(r"[0-9a-f]+\.(proxy\.mp4|strip\.jpg)", name):
            raise HTTPException(404, "No such media.")
        path = svc.dir(project_id) / "media" / name
        if not path.exists():
            raise HTTPException(404, "No such media.")
        return FileResponse(path)

