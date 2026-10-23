"""Claude access. Two interchangeable backends behind one small interface.

- `subscription`: Claude Agent SDK, which runs the bundled Claude Code binary and reuses the user's Claude login.
  For personal, single-user use only.
- `api`: Anthropic SDK with ANTHROPIC_API_KEY, billed per token. Requires the `api` extra.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from media_analysis.errors import MediaAnalysisError


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Image:
    path: Path


Block = Text | Image

Activity = Callable[[str], None]
"""Receives short live status lines during a call, such as "thinking" or "writing, 1,240 chars"."""


def _media_type(path: Path) -> str:
    return {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/jpeg")


def _to_api_blocks(content: list[Block]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, Text):
            out.append({"type": "text", "text": block.text})
        else:
            data = base64.standard_b64encode(block.path.read_bytes()).decode("ascii")
            out.append({"type": "image", "source": {"type": "base64", "media_type": _media_type(block.path), "data": data}})
    return out


class TransientLLMError(MediaAnalysisError):
    """Rate limits, overload, dropped connections: worth retrying."""


class LLM(Protocol):
    model: str | None
    effort: str | None

    def generate_json(
        self, system: str, content: list[Block], schema: dict[str, Any], on_activity: Activity | None = None,
    ) -> dict[str, Any]: ...

    def generate_text(self, system: str, content: list[Block], on_activity: Activity | None = None) -> str: ...


def _with_retries(fn, attempts: int = 3):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except TransientLLMError as e:
            last = e
            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))
    assert last is not None
    raise last


class _ActivityTracker:
    """Turns raw API stream events into throttled status lines."""

    def __init__(self, on_activity: Activity | None, interval: float = 0.5) -> None:
        self.on_activity = on_activity
        self.interval = interval
        self.chars = 0
        self.phase = "waiting for Claude"
        self._last = 0.0

    def event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "content_block_start":
            block_type = event.get("content_block", {}).get("type")
            self.phase = "thinking" if block_type in ("thinking", "redacted_thinking") else "writing"
        elif event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            text = delta.get("text") or delta.get("partial_json") or ""
            if text:
                self.phase = "writing"
                self.chars += len(text)
        self.emit()

    def emit(self, force: bool = False) -> None:
        now = time.monotonic()
        if self.on_activity and (force or now - self._last >= self.interval):
            self._last = now
            self.on_activity(f"{self.phase}, {self.chars:,} chars" if self.chars else self.phase)


class SubscriptionLLM:
    """Claude via the Agent SDK with all tools disabled, so each call behaves like a plain model call.

    `model` and `effort` left as None use the account defaults, as Claude Code does.
    """

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        self.model = model
        self.effort = effort
        # An API key in the environment would take precedence over the login and bill the API silently.
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def generate_json(
        self, system: str, content: list[Block], schema: dict[str, Any], on_activity: Activity | None = None,
    ) -> dict[str, Any]:
        result = _with_retries(lambda: asyncio.run(self._query(system, content, schema, on_activity)))
        if not isinstance(result, dict):
            raise MediaAnalysisError("Claude returned no structured output.")
        return result

    def generate_text(self, system: str, content: list[Block], on_activity: Activity | None = None) -> str:
        result = _with_retries(lambda: asyncio.run(self._query(system, content, None, on_activity)))
        return str(result or "")

    async def _query(
        self, system: str, content: list[Block], schema: dict[str, Any] | None, on_activity: Activity | None,
    ) -> Any:
        from claude_agent_sdk import (
            ClaudeAgentOptions, ClaudeSDKError, ResultMessage, StreamEvent, SystemMessage, query,
        )

        options = ClaudeAgentOptions(
            tools=[],
            setting_sources=[],
            system_prompt=system,
            model=self.model,
            effort=self.effort,  # type: ignore[arg-type]
            max_turns=4,
            verbatim_prompts=True,
            include_partial_messages=on_activity is not None,
            output_format={"type": "json_schema", "schema": schema} if schema else None,
            # Each call is self-contained; without this Claude Code saves every transcript to the user's Claude history.
            extra_args={"no-session-persistence": None},
        )
        blocks = _to_api_blocks(content)

        # Prompts go through stdin as a streamed message: Windows caps command lines at ~32 KB.
        async def prompt() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "user", "message": {"role": "user", "content": blocks}, "parent_tool_use_id": None}

        tracker = _ActivityTracker(on_activity)
        tracker.emit(force=True)
        result: ResultMessage | None = None
        try:
            async for message in query(prompt=prompt(), options=options):
                if isinstance(message, StreamEvent):
                    tracker.event(message.event)
                elif isinstance(message, SystemMessage) and message.subtype == "init" and on_activity:
                    on_activity(f"model {message.data.get('model', '?')}")
                elif isinstance(message, ResultMessage):
                    result = message
        except ClaudeSDKError as e:
            raise TransientLLMError(f"Claude call failed: {e}") from e
        if result is None:
            raise TransientLLMError("Claude returned no result.")
        if result.is_error:
            detail = "; ".join(result.errors or []) or result.subtype
            status = result.api_error_status
            if status:
                detail += f" (HTTP {status})"
            transient = status is None or status == 429 or status >= 500
            raise (TransientLLMError if transient else MediaAnalysisError)(f"Claude call failed: {detail}")
        return result.structured_output if schema else result.result


class ApiLLM:
    """Claude via the Messages API (ANTHROPIC_API_KEY). The API has no account default, so a model is always set."""

    DEFAULT_MODEL = "claude-opus-5-5"

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise MediaAnalysisError("The api backend needs the extra: `uv sync --extra api`.") from e
        self.model = model or self.DEFAULT_MODEL
        self.effort = effort
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    def _create(
        self, system: str, content: list[Block], schema: dict[str, Any] | None, on_activity: Activity | None,
    ) -> str:
        output_config: dict[str, Any] = {}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 64000,
            "system": system,
            "messages": [{"role": "user", "content": _to_api_blocks(content)}],
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if output_config:
            kwargs["output_config"] = output_config
        tracker = _ActivityTracker(on_activity)
        tracker.emit(force=True)
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                for event in stream:
                    if on_activity:
                        tracker.event(event.to_dict())
                message = stream.get_final_message()
        except self._anthropic.APIStatusError as e:
            if e.status_code == 429 or e.status_code >= 500:
                raise TransientLLMError(f"Claude API error {e.status_code}: {e.message}") from e
            raise MediaAnalysisError(f"Claude API rejected the request ({e.status_code}): {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise TransientLLMError(f"Claude API connection error: {e}") from e
        if message.stop_reason == "refusal":
            raise MediaAnalysisError("Claude declined the request.")
        return "".join(b.text for b in message.content if b.type == "text")

    def generate_json(
        self, system: str, content: list[Block], schema: dict[str, Any], on_activity: Activity | None = None,
    ) -> dict[str, Any]:
        text = _with_retries(lambda: self._create(system, content, schema, on_activity))
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise MediaAnalysisError("Claude returned invalid JSON.") from e

    def generate_text(self, system: str, content: list[Block], on_activity: Activity | None = None) -> str:
        return _with_retries(lambda: self._create(system, content, None, on_activity))


def create_llm(backend: str, model: str | None = None, effort: str | None = None) -> LLM:
    if backend == "api":
        return ApiLLM(model, effort)
    return SubscriptionLLM(model, effort)
