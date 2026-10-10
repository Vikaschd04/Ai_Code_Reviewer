"""Bounded investigation loop: planner → investigator (model + tools) → submission.

Hard limits: model calls, tool calls, total tokens (provider-reported, or estimated from request
size when a provider reports none), optional cost (only with owner-configured prices), wall time
and cancellation. The last model call is reserved for the final ``submit_*`` tool, so a run that
reaches its limits still returns what it found. Malformed submissions get a small, bounded repair
allowance. The step log records actions and outcomes, never the model's private reasoning.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from crp_analysis.ai.config import Limits, Prices
from crp_analysis.ai.models import (
    Message,
    ModelRequest,
    ModelResponse,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from crp_analysis.ai.prompts import Task
from crp_analysis.ai.providers import ModelClient
from crp_analysis.ai.results import (
    SUBMIT_ANSWER,
    SUBMIT_FIXES,
    SUBMIT_PLAN,
    SUBMIT_REVIEW,
    SubmittedAnswer,
    SubmittedFixes,
    SubmittedPlan,
    SubmittedReview,
)
from crp_analysis.ai.tools import READ_TOOLS, ToolExecutor

StopKind = Literal[
    "submitted", "budget_exhausted", "timeout", "cancelled", "no_result", "provider_error"
]
_MAX_REPAIRS = 2
_FINAL_RESERVE_TOKENS = 6000


@dataclass(frozen=True, slots=True)
class CallRecord:
    sequence: int
    status: Literal["ok", "error"]
    usage: Usage
    estimated_tokens: int  # used for budgeting when the provider reported no usage
    cost_usd: float | None
    latency_ms: int
    attempts: int
    request_id: str | None
    model: str
    error_code: str | None = None


@dataclass
class InvestigationResult:
    stop: StopKind
    answer: SubmittedAnswer | None = None
    review: SubmittedReview | None = None
    fixes: SubmittedFixes | None = None
    plan: SubmittedPlan | None = None
    calls: list[CallRecord] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    excerpts: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    error: ProviderError | None = None
    limitation: str | None = None

    @property
    def usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total

    @property
    def budget_tokens(self) -> int:
        return sum(
            c.usage.total_tokens if c.usage.reported else c.estimated_tokens for c in self.calls
        )

    @property
    def cost_usd(self) -> float | None:
        costs = [c.cost_usd for c in self.calls]
        return None if any(c is None for c in costs) else float(sum(c or 0.0 for c in costs))


def _estimate_tokens(request: ModelRequest) -> int:
    """Rough upper estimate (4 characters per token) used only to budget before a call."""
    size = len(request.system) + sum(
        len(json.dumps(t.input_schema)) + len(t.description) for t in request.tools
    )
    for message in request.messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                size += len(block.text)
            elif isinstance(block, ToolResultBlock):
                size += len(block.content)
            else:
                size += len(json.dumps(block.input))
    return size // 4 + 1


def _serialize(message: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append({"type": "tool_use", "name": block.name, "input": block.input})
        else:
            blocks.append(
                {"type": "tool_result", "content": block.content, "is_error": block.is_error}
            )
    return {"role": message.role, "content": blocks}


async def investigate(
    client: ModelClient,
    task: Task,
    tools: ToolExecutor,
    limits: Limits,
    prices: Prices,
    *,
    token_allowance: int | None = None,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
    on_call: Callable[[CallRecord], Awaitable[None]] | None = None,
    keep_transcript: bool = True,
) -> InvestigationResult:
    final = {
        SUBMIT_ANSWER.name: SUBMIT_ANSWER,
        SUBMIT_REVIEW.name: SUBMIT_REVIEW,
        SUBMIT_FIXES.name: SUBMIT_FIXES,
        SUBMIT_PLAN.name: SUBMIT_PLAN,
    }[task.final_tool]
    specs = (*READ_TOOLS, final)
    max_tokens = (
        min(limits.max_tokens, token_allowance)
        if token_allowance is not None
        else limits.max_tokens
    )
    deadline = time.monotonic() + limits.timeout_seconds
    messages: list[Message] = [Message("user", (TextBlock(task.first_message),))]
    result = InvestigationResult(stop="no_result")
    tool_calls = 0
    repairs = 0
    force_final = False

    def step(action: str, detail: str, outcome: str) -> None:
        result.steps.append(
            {
                "n": len(result.steps) + 1,
                "action": action,
                "detail": detail[:240],
                "outcome": outcome[:240],
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )

    step("plan", f"{task.kind}: {len(task.first_message)} characters of context", "ready")
    while True:
        if cancelled is not None and await cancelled():
            result.stop = "cancelled"
            result.limitation = "Stopped on request before the investigation finished."
            break
        calls_left = limits.max_model_calls - len(result.calls)
        if calls_left <= 0:
            result.stop = "budget_exhausted"
            result.limitation = (
                f"Stopped after {limits.max_model_calls} model calls (the per-run limit)."
            )
            break
        if calls_left == 1:
            force_final = True
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 1:
            result.stop = "timeout"
            result.limitation = f"Stopped at the {limits.timeout_seconds}s time limit."
            break
        request = ModelRequest(
            system=task.system,
            messages=tuple(messages),
            tools=specs,
            tool_choice=final.name if force_final else "auto",
            max_output_tokens=limits.max_output_tokens,
        )
        estimate = _estimate_tokens(request)
        projected = result.budget_tokens + estimate + limits.max_output_tokens
        if projected > max_tokens:
            if (
                force_final
                or result.budget_tokens + estimate + limits.max_output_tokens
                > max_tokens + _FINAL_RESERVE_TOKENS
            ):
                result.stop = "budget_exhausted"
                result.limitation = f"Stopped at the token budget ({max_tokens:,} tokens)."
                break
            force_final = True
            request = ModelRequest(
                system=task.system,
                messages=tuple(messages),
                tools=specs,
                tool_choice=final.name,
                max_output_tokens=limits.max_output_tokens,
            )
        if limits.max_cost_usd is not None and prices.known:
            spent = result.cost_usd or 0.0
            next_cost = prices.cost(estimate, limits.max_output_tokens) or 0.0
            if spent + next_cost > limits.max_cost_usd:
                if force_final:
                    result.stop = "budget_exhausted"
                    result.limitation = f"Stopped at the cost limit (${limits.max_cost_usd:.2f})."
                    break
                force_final = True
        sequence = len(result.calls) + 1
        try:
            response: ModelResponse = await asyncio.wait_for(
                client.complete(request), timeout=max(1.0, remaining_time)
            )
        except TimeoutError:
            record = CallRecord(
                sequence,
                "error",
                Usage(reported=False),
                estimate,
                prices.cost(estimate, 0),
                0,
                1,
                None,
                client.model,
                "timeout",
            )
            result.calls.append(record)
            if on_call is not None:
                await on_call(record)
            result.stop = "timeout"
            result.limitation = f"Stopped at the {limits.timeout_seconds}s time limit."
            step("model", f"call {sequence}", "timed out")
            break
        except ProviderError as exc:
            record = CallRecord(
                sequence,
                "error",
                Usage(reported=False),
                0,
                None,
                0,
                exc.attempts,
                exc.request_id,
                client.model,
                exc.code.value,
            )
            result.calls.append(record)
            if on_call is not None:
                await on_call(record)
            result.stop = "provider_error"
            result.error = exc
            step("model", f"call {sequence}", f"provider error: {exc.code.value}")
            break
        cost = (
            prices.cost(response.usage.input_tokens, response.usage.output_tokens)
            if response.usage.reported
            else prices.cost(estimate, limits.max_output_tokens)
        )
        record = CallRecord(
            sequence,
            "ok",
            response.usage,
            estimate + (0 if response.usage.reported else limits.max_output_tokens),
            cost,
            response.latency_ms,
            response.attempts,
            response.request_id,
            response.model,
        )
        result.calls.append(record)
        if on_call is not None:
            await on_call(record)
        assistant = Message(
            "assistant", response.content or (TextBlock(response.text or "(no text)"),)
        )
        messages.append(assistant)
        if keep_transcript:
            result.transcript.append(_serialize(assistant))
        step(
            "model",
            f"call {sequence}",
            f"{len(response.tool_calls)} tool call(s), stop={response.stop_reason.value}",
        )

        if not response.tool_calls:
            if repairs >= _MAX_REPAIRS:
                result.limitation = "The model did not submit a structured result."
                break
            repairs += 1
            force_final = True
            reminder = Message("user", (TextBlock(f"Call {final.name} now with your result."),))
            messages.append(reminder)
            continue

        results: list[ToolResultBlock] = []
        submitted = False
        for call in response.tool_calls:
            if call.name == final.name:
                try:
                    if final is SUBMIT_ANSWER:
                        result.answer = SubmittedAnswer.model_validate(call.input)
                    elif final is SUBMIT_FIXES:
                        result.fixes = SubmittedFixes.model_validate(call.input)
                    elif final is SUBMIT_PLAN:
                        result.plan = SubmittedPlan.model_validate(call.input)
                    else:
                        result.review = SubmittedReview.model_validate(call.input)
                    submitted = True
                    step("submit", final.name, "accepted")
                    results.append(ToolResultBlock(call.id, "Received."))
                except ValidationError as exc:
                    problems = "; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                        for e in exc.errors()[:6]
                    )
                    step("submit", final.name, f"rejected: {problems}")
                    results.append(
                        ToolResultBlock(
                            call.id,
                            f"Invalid submission: {problems}. Fix it and call {final.name} again.",
                            is_error=True,
                        )
                    )
                continue
            tool_calls += 1
            if tool_calls > limits.max_tool_calls:
                force_final = True
                results.append(
                    ToolResultBlock(
                        call.id, f"Tool budget used up. Call {final.name} now.", is_error=True
                    )
                )
                step("tool", call.name, "refused: tool budget used up")
                continue
            outcome = await tools.execute(call.name, call.input)
            results.append(ToolResultBlock(call.id, outcome.content, is_error=outcome.is_error))
            step("tool", outcome.summary, "error" if outcome.is_error else "ok")
        if submitted:
            result.stop = "submitted"
            break
        if any(b.is_error for b in results) and all(
            c.name == final.name for c in response.tool_calls
        ):
            repairs += 1
            if repairs > _MAX_REPAIRS:
                result.limitation = "The model's submission stayed invalid after repairs."
                break
        tool_message = Message("user", tuple(results))
        messages.append(tool_message)
        if keep_transcript:
            result.transcript.append(_serialize(tool_message))
    result.excerpts = list(tools.excerpts)
    if keep_transcript:
        result.transcript.insert(0, _serialize(messages[0]))
    return result
