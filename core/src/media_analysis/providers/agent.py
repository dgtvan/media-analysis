"""Claude with tools: a conversation turn in which Claude may call the tools a workflow offers, one at a time, before
it answers. Used for chat-driven changes (e.g. to an edit plan).

Tools are plain functions with a Pydantic model for their arguments. Arguments are validated before the function
runs; a validation error or a ToolError goes back to Claude as the tool's result, so it can correct itself. Every
step is reported as a ChatEvent, for a chat panel to show live.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from media_analysis.engine.errors import MediaAnalysisError
from media_analysis.engine.usage import Usage
from media_analysis.providers.llm import Block, TransientLLMError, _to_api_blocks


class ToolError(Exception):
    """A tool refused its arguments; the message goes back to Claude."""


@dataclass
class AgentTool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[Any], str]
    """Gets the validated arguments, returns a short text result for Claude."""

    def call(self, raw: dict[str, Any]) -> tuple[str, bool]:
        """(result text, is_error)."""
        try:
            args = self.params.model_validate(raw)
        except ValidationError as e:
            return f"Invalid arguments: {e.errors(include_url=False)}", True
        try:
            return self.handler(args), False
        except ToolError as e:
            return str(e), True


@dataclass
class ChatEvent:
    kind: str
    """assistant.delta (text so far grows), tool.start, tool.end, assistant.done."""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    text: str
    tool_calls: list[dict[str, Any]]
    usage: Usage | None = None


EventSink = Callable[[ChatEvent], None]


def run_agent(
    *, system: str, content: list[Block], tools: list[AgentTool], backend: str = "subscription",
    model: str | None = None, effort: str | None = None, profile: Path | None = None,
    on_event: EventSink | None = None, max_turns: int = 16,
) -> AgentResult:
    emit = on_event or (lambda e: None)
    # Not retried as a whole: tools may already have changed things when a turn fails part way.
    if backend == "api":
        return _run_api(system, content, tools, model, effort, emit, max_turns)
    return asyncio.run(_run_subscription(system, content, tools, model, effort, profile, emit, max_turns))


def _schema(params: type[BaseModel]) -> dict[str, Any]:
    schema = params.model_json_schema()
    schema.pop("title", None)
    return schema


async def _run_subscription(
    system: str, content: list[Block], tools: list[AgentTool], model: str | None, effort: str | None,
    profile: Path | None, emit: EventSink, max_turns: int,
) -> AgentResult:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKError, ResultMessage, StreamEvent, TextBlock, create_sdk_mcp_server,
        query, tool,
    )

    os.environ.pop("ANTHROPIC_API_KEY", None)
    calls: list[dict[str, Any]] = []

    def wrap(t: AgentTool):
        @tool(t.name, t.description, _schema(t.params))
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            emit(ChatEvent("tool.start", {"tool": t.name, "args": args}))
            text, is_error = t.call(args)
            calls.append({"tool": t.name, "args": args, "result": text, "error": is_error})
            emit(ChatEvent("tool.end", {"tool": t.name, "result": text, "error": is_error}))
            return {"content": [{"type": "text", "text": text}], "is_error": is_error}
        return handler

    server = create_sdk_mcp_server("work", tools=[wrap(t) for t in tools])
    options = ClaudeAgentOptions(
        tools=[],
        allowed_tools=[f"mcp__work__{t.name}" for t in tools],
        mcp_servers={"work": server},
        setting_sources=[],
        system_prompt=system,
        model=model,
        effort=effort,  # type: ignore[arg-type]
        max_turns=max_turns,
        verbatim_prompts=True,
        include_partial_messages=True,
        permission_mode="bypassPermissions",
        extra_args={"no-session-persistence": None},
        env={"CLAUDE_CONFIG_DIR": str(profile)} if profile else {},
    )
    blocks = _to_api_blocks(content)

    async def prompt() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "user", "message": {"role": "user", "content": blocks}, "parent_tool_use_id": None}

    text = ""
    result: ResultMessage | None = None
    try:
        async for message in query(prompt=prompt(), options=options):
            if isinstance(message, StreamEvent):
                ev = message.event
                if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    text += ev["delta"].get("text", "")
                    emit(ChatEvent("assistant.delta", {"text": text}))
            elif isinstance(message, AssistantMessage):
                # A new assistant message after tool calls starts a new paragraph of the answer.
                if any(isinstance(b, TextBlock) for b in message.content) and text and not text.endswith("\n"):
                    text += "\n\n"
            elif isinstance(message, ResultMessage):
                result = message
    except ClaudeSDKError as e:
        raise TransientLLMError(f"Claude call failed: {e}") from e
    if result is None:
        raise TransientLLMError("Claude returned no result.")
    if result.is_error and not text:
        raise MediaAnalysisError(f"Claude call failed: {'; '.join(result.errors or []) or result.subtype}")
    final = (result.result or text).strip()
    emit(ChatEvent("assistant.done", {"text": final}))
    usage = Usage.from_api(result.usage, result.total_cost_usd) if result.usage else None
    return AgentResult(text=final, tool_calls=calls, usage=usage)


def _run_api(
    system: str, content: list[Block], tools: list[AgentTool], model: str | None, effort: str | None,
    emit: EventSink, max_turns: int,
) -> AgentResult:
    try:
        import anthropic
    except ImportError as e:
        raise MediaAnalysisError("The api backend needs the extra: `uv sync --extra api`.") from e
    client = anthropic.Anthropic()
    by_name = {t.name: t for t in tools}
    api_tools = [{"name": t.name, "description": t.description, "input_schema": _schema(t.params)} for t in tools]
    messages: list[dict[str, Any]] = [{"role": "user", "content": _to_api_blocks(content)}]
    calls: list[dict[str, Any]] = []
    usage = Usage()
    text = ""
    for _ in range(max_turns):
        kwargs: dict[str, Any] = {}
        if effort:
            kwargs["output_config"] = {"effort": effort}
        response = client.messages.create(
            model=model or "claude-opus-5-5", max_tokens=16000, system=system, tools=api_tools, messages=messages,
            **kwargs,
        )
        usage += Usage.from_api(response.usage)
        results = []
        for block in response.content:
            if block.type == "text":
                text += ("\n\n" if text else "") + block.text
                emit(ChatEvent("assistant.delta", {"text": text}))
            elif block.type == "tool_use":
                t = by_name.get(block.name)
                emit(ChatEvent("tool.start", {"tool": block.name, "args": block.input}))
                out, is_error = t.call(block.input) if t else (f"Unknown tool {block.name}", True)
                calls.append({"tool": block.name, "args": block.input, "result": out, "error": is_error})
                emit(ChatEvent("tool.end", {"tool": block.name, "result": out, "error": is_error}))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
        if response.stop_reason != "tool_use":
            break
        messages += [{"role": "assistant", "content": [b.model_dump() for b in response.content]},
                     {"role": "user", "content": results}]
    emit(ChatEvent("assistant.done", {"text": text.strip()}))
    return AgentResult(text=text.strip(), tool_calls=calls, usage=usage)


def describe_calls(calls: list[dict[str, Any]]) -> str:
    """The tool calls of a turn as short lines, e.g. for a chat history."""
    return "\n".join(f"- {c['tool']}({json.dumps(c['args'], ensure_ascii=False)}) -> {c['result']}" for c in calls)
