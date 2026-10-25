"""Service mode: the core as a small local web server for an app's presentation layer (HTML in a WebView2 window, or
anything else that speaks HTTP).

- Listens on 127.0.0.1 only, on a free port, and announces it as one JSON line on stdout:
  `{"event": "listening", "url": "http://127.0.0.1:PORT/?token=..."}`.
- Every request needs the token given at start (query `token=` or header `X-Token`), so other programs on the
  machine can't use it.
- Live events (progress, chat) go to WebSocket subscribers per channel (`/api/events/{channel}`).
- Long work runs as a child process of the CLI (`python -m media_analysis ... --json`), so it can be stopped at any
  time and can't take the server down; its JSON events are passed on to the channel.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class EventHub:
    """Fan-out of JSON events to WebSocket subscribers, per channel. `publish` may be called from any thread."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(channel, set()).add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs.get(channel, set()).discard(q)

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        for q in list(self._subs.get(channel, ())):
            self._loop.call_soon_threadsafe(q.put_nowait, event)


class Job:
    """A CLI run in a child process whose JSON events go to a channel."""

    def __init__(self, channel: str, args: list[str], hub: EventHub, env: dict[str, str] | None = None,
                 on_exit: Callable[[int], None] | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.channel = channel
        self.hub = hub
        self.last: dict[str, Any] = {}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "media_analysis", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, **(env or {}), "PYTHONIOENCODING": "utf-8"}, creationflags=_NO_WINDOW,
        )
        self._on_exit = on_exit
        self._on_event = on_event
        self.stopped = False
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if event.get("event") in ("stage_started", "progress", "stage_done", "stage_skipped", "activity"):
                self.last = event
            if self._on_event:
                self._on_event(event)
            self.hub.publish(self.channel, {"source": "job", **event})
        code = self.proc.wait()
        exit_event = {"event": "job_exit", "exit_code": code, "stopped": self.stopped}
        if self._on_event:
            self._on_event(exit_event)
        self.hub.publish(self.channel, {"source": "job", **exit_event})
        if self._on_exit:
            self._on_exit(code)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.running:
            self.stopped = True
            if sys.platform == "win32":
                # /T also ends ffmpeg and Claude Code started by the run.
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=_NO_WINDOW)
            else:
                self.proc.kill()


def create_app(token: str, hub: EventHub) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.attach(asyncio.get_running_loop())
        yield

    app = FastAPI(title="media-analysis service", version="1", lifespan=lifespan)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        given = request.headers.get("x-token") or request.query_params.get("token") or request.cookies.get("token")
        if not secrets.compare_digest(given or "", token):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        response = await call_next(request)
        # The page keeps working after the token leaves the address bar (links to media, reloads).
        response.set_cookie("token", token, httponly=True, samesite="strict")
        return response

    @app.websocket("/api/events/{channel}")
    async def events(ws: WebSocket, channel: str) -> None:
        if not secrets.compare_digest(ws.query_params.get("token") or ws.cookies.get("token") or "", token):
            await ws.close(code=4403)
            return
        await ws.accept()
        q = hub.subscribe(channel)
        try:
            while True:
                await ws.send_json(await q.get())
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(channel, q)

    return app


def mount_ui(app: FastAPI, ui_dir: Path | None) -> None:
    """Serves the presentation's files at "/" (after the API routes, so they take precedence)."""
    if ui_dir is not None and ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app: FastAPI, token: str, port: int | None = None) -> None:
    import uvicorn

    port = port or free_port()
    print(json.dumps({"event": "listening", "port": port, "url": f"http://127.0.0.1:{port}/?token={token}"}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
