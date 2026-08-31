from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantMessage:
    content: str


@dataclass(frozen=True)
class ReasoningSummary:
    content: str


@dataclass(frozen=True)
class FinalAnswer:
    content: str


ResponseItem = AssistantMessage | ReasoningSummary | ToolCall | FinalAnswer
