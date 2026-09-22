"""The one place this project talks to Claude.

Every model call in the app goes through :class:`ClaudeClient`. That gives one
seam for:

* structured outputs: :meth:`ClaudeClient.extract` asks the API for JSON that
  matches a pydantic schema and returns a validated model instance, so no
  caller ever parses free text or guesses at a missing key;
* effort per call: extraction and classification run at ``low``, the board
  writer at ``medium``; the caller decides, the wrapper passes it through;
* prompt caching: the system prompt is marked as a cache breakpoint, so batches
  in the same run that share a system prompt pay for it once;
* retries and timeouts: the SDK retries rate limits and 5xx with backoff, and a
  per-request timeout keeps an unattended run from hanging;
* usage accounting: every call is recorded with its tag, token counts, cache
  hits, and estimated cost, and logged on one line so the daily log shows where
  the spend went.

The SDK client is injectable, which is what makes the LLM layer testable
without network access (see ``tests/conftest.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import anthropic
from anthropic.types import OutputConfigParam, TextBlockParam
from pydantic import BaseModel

log = logging.getLogger(__name__)
usage_log = logging.getLogger("claude-usage")

DEFAULT_MODEL = "claude-sonnet-5"

# USD per million tokens, (input, output). Cache reads are billed at 10% of
# input and cache writes at 125%; those multipliers are applied in Usage.cost.
PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

Effort = Literal["low", "medium", "high", "xhigh", "max"]

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(RuntimeError):
    """Claude answered, but not with something the caller can use.

    Raised when the output was cut off at ``max_tokens``, when structured output
    failed validation, or when the model returned no text at all. Callers should
    treat this as a failed batch and retry later, never as an empty result.
    """


@dataclass(frozen=True)
class Usage:
    tag: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    stop_reason: str

    @property
    def cost_usd(self) -> float:
        rate_in, rate_out = PRICING.get(self.model, (0.0, 0.0))
        per_token_in = rate_in / 1_000_000
        per_token_out = rate_out / 1_000_000
        return (
            self.input_tokens * per_token_in
            + self.cache_read_tokens * per_token_in * 0.10
            + self.cache_write_tokens * per_token_in * 1.25
            + self.output_tokens * per_token_out
        )


@dataclass
class UsageTracker:
    """Accumulates per-call usage for a run and can summarise it by tag."""

    records: list[Usage] = field(default_factory=list)

    def record(self, usage: Usage) -> None:
        self.records.append(usage)
        usage_log.info(
            "%s: %s in=%d out=%d cache_read=%d cache_write=%d stop=%s cost=$%.4f",
            usage.tag,
            usage.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            usage.stop_reason,
            usage.cost_usd,
        )

    def by_tag(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for u in self.records:
            row = out.setdefault(
                u.tag,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            row["calls"] += 1
            row["input_tokens"] += u.input_tokens
            row["output_tokens"] += u.output_tokens
            row["cache_read_tokens"] += u.cache_read_tokens
            row["cost_usd"] += u.cost_usd
        return out

    @property
    def total_cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.records)


class ClaudeClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        client: anthropic.Anthropic | None = None,
        tracker: UsageTracker | None = None,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.tracker = tracker or UsageTracker()
        self._client = client or anthropic.Anthropic(max_retries=max_retries, timeout=timeout)

    # ── public API ──────────────────────────────────────────────────────────

    def extract(
        self,
        tag: str,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: Effort = "low",
        max_tokens: int = 16000,
    ) -> T:
        """Return a validated instance of ``schema`` for the given prompt.

        Uses the Messages API structured-output format, so the response is
        constrained to the schema server-side and validated by pydantic here.
        """
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=self._system_blocks(system),
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config=OutputConfigParam(effort=effort),
        )
        self._record(tag, response)
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(f"{tag}: output hit max_tokens={max_tokens} and was cut off")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMOutputError(f"{tag}: response contained no parseable structured output")
        return parsed

    def text(
        self,
        tag: str,
        *,
        system: str,
        user: str,
        effort: Effort = "medium",
        max_tokens: int = 8000,
    ) -> str:
        """Return the first text block of a free-form completion."""
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self._system_blocks(system),
            messages=[{"role": "user", "content": user}],
            output_config=OutputConfigParam(effort=effort),
        )
        self._record(tag, response)
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(f"{tag}: output hit max_tokens={max_tokens} and was cut off")
        for block in response.content:
            if block.type == "text" and block.text.strip():
                return block.text
        raise LLMOutputError(f"{tag}: response contained no text")

    # ── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _system_blocks(system: str) -> list[TextBlockParam]:
        # One breakpoint on the whole system prompt. Below the model's minimum
        # cacheable prefix this is a no-op, which is harmless.
        return [TextBlockParam(type="text", text=system, cache_control={"type": "ephemeral"})]

    def _record(self, tag: str, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.tracker.record(
            Usage(
                tag=tag,
                model=getattr(response, "model", self.model),
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                stop_reason=str(getattr(response, "stop_reason", "?")),
            )
        )
