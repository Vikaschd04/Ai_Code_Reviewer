"""Provider-neutral model contract: messages with text, tool calls and tool results; usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


Block = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["user", "assistant"]
    content: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


ToolChoice = Literal["auto", "any"] | str  # a tool name forces that tool


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    tool_choice: ToolChoice = "auto"
    max_output_tokens: int = 4096


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts as reported by the provider (``reported`` is False when it sent none)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reported: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.reported and other.reported,
        )


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolUseBlock, ...]
    stop_reason: StopReason
    usage: Usage
    model: str
    request_id: str | None = None
    latency_ms: int = 0
    attempts: int = 1
    content: tuple[Block, ...] = field(default=())


class ProviderErrorCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    MALFORMED_RESPONSE = "malformed_response"


class ProviderError(Exception):
    """A failed provider call; ``message`` never contains the API key or request payload."""

    def __init__(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        request_id: str | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.request_id = request_id
        self.attempts = attempts
