"""Token usage of Claude calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Usage:
    """Tokens used by one or more Claude calls.

    `cost_usd` is the price Claude Code reports for the calls at API rates; with the subscription it is only an
    estimate (calls aren't billed per token). The API backend doesn't report one.
    """

    input_tokens: int = 0
    """Input that wasn't read from or written to the prompt cache."""
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None

    @classmethod
    def from_api(cls, usage: Any, cost_usd: float | None = None) -> Usage:
        """From an Anthropic `usage` object or dict (the Agent SDK passes a dict)."""
        def get(name: str) -> int:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            return int(value or 0)
        return cls(get("input_tokens"), get("cache_read_input_tokens"), get("cache_creation_input_tokens"),
                   get("output_tokens"), cost_usd)

    def __add__(self, other: Usage) -> Usage:
        cost = None if self.cost_usd is None and other.cost_usd is None else (self.cost_usd or 0) + (other.cost_usd or 0)
        return Usage(self.input_tokens + other.input_tokens, self.cache_read_tokens + other.cache_read_tokens,
                     self.cache_write_tokens + other.cache_write_tokens, self.output_tokens + other.output_tokens, cost)

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def describe(self) -> str:
        """E.g. "12,345 tokens in (10,000 from cache), 1,234 out, ~$0.05 at API rates"."""
        text = f"{self.total_input:,} tokens in"
        if self.cache_read_tokens:
            text += f" ({self.cache_read_tokens:,} from cache)"
        text += f", {self.output_tokens:,} out"
        if self.cost_usd is not None:
            text += f", ~${self.cost_usd:.2f} at API rates"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens, "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens, "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4) if self.cost_usd is not None else None,
        }
