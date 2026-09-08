from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    USER_INPUT = "user_input"
    CONTEXT_BUILT = "context_built"
    CONTEXT_OBSERVED = "context_observed"
    CONTEXT_BUDGET_CHECKED = "context_budget_checked"
    CONTEXT_COMPACTION_REQUIRED = "context_compaction_required"
    CONTEXT_COMPACTION_APPLIED = "context_compaction_applied"
    CONTEXT_COMPACTION_FAILED = "context_compaction_failed"
    MODEL_REQUEST = "model_request"
    MODEL_RETRY_SCHEDULED = "model_retry_scheduled"
    MODEL_RETRY_EXHAUSTED = "model_retry_exhausted"
    MODEL_RESPONSE = "model_response"
    MODEL_RESPONSE_ITEM = "model_response_item"
    TASK_VERIFICATION_STARTED = "task_verification_started"
    TASK_VERIFICATION_RESULT = "task_verification_result"
    TOOL_APPROVAL_DECIDED = "tool_approval_decided"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_OUTPUT_TRUNCATED = "tool_output_truncated"
    POSSIBLE_LOOP_DETECTED = "possible_loop_detected"
    AGENT_CANCELLED = "agent_cancelled"
    AGENT_STOPPED = "agent_stopped"
    RUNTIME_ERROR = "runtime_error"


@dataclass
class AgentEvent:
    type: EventType
    step: int
    data: dict[str, Any]
    timestamp: datetime

    @classmethod
    def create(cls, type: EventType, step: int, data: dict[str, Any]) -> "AgentEvent":
        return cls(type=type, step=step, data=data, timestamp=datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["type"] = self.type.value
        result["timestamp"] = self.timestamp.isoformat()
        return result
