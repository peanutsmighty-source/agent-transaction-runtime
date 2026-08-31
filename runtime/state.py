from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from .events import AgentEvent


class AgentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Message:
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolExecution:
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextItemStat:
    id: str
    type: str
    token_count: int

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


@dataclass
class ContextStats:
    item_count: int = 0
    estimated_tokens: int = 0
    tokens_by_type: dict[str, int] = field(default_factory=dict)
    largest_items: list[ContextItemStat] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_count": self.item_count,
            "estimated_tokens": self.estimated_tokens,
            "tokens_by_type": self.tokens_by_type,
            "largest_items": [item.to_dict() for item in self.largest_items],
        }


@dataclass
class ContextBudgetStatus:
    current_tokens: int = 0
    effective_input_tokens: int = 0
    compact_trigger_tokens: int = 0
    should_compact: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


@dataclass
class ModelUsageTotals:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, usage: dict[str, int]) -> None:
        self.input_tokens += usage["input_tokens"]
        self.cached_input_tokens += usage["cached_input_tokens"]
        self.output_tokens += usage["output_tokens"]
        self.reasoning_tokens += usage["reasoning_tokens"]

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class AgentState:
    task: str
    status: AgentStatus = AgentStatus.RUNNING
    step: int = 0
    messages: list[Message] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    tool_history: list[ToolExecution] = field(default_factory=list)
    context_stats: ContextStats = field(default_factory=ContextStats)
    context_budget: ContextBudgetStatus = field(default_factory=ContextBudgetStatus)
    model_usage: ModelUsageTotals = field(default_factory=ModelUsageTotals)
    final_answer: str | None = None
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status.value,
            "step_count": self.step,
            "model_calls": len([event for event in self.events if event.type.value == "model_request"]),
            "tool_calls": len(self.tool_history),
            "compactions": len([
                event for event in self.events
                if event.type.value == "context_compaction_applied"
            ]),
            "context_stats": self.context_stats.to_dict(),
            "context_budget": self.context_budget.to_dict(),
            "model_usage": self.model_usage.to_dict(),
            "final_answer": self.final_answer,
            "error": self.error,
        }
