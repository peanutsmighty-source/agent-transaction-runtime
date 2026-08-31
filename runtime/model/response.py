from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..actions import FinalAnswer, ResponseItem, ToolCall


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ModelResponse:
    items: list[ResponseItem]
    response_id: str | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("model_response_items_must_not_be_empty")
        final_count = sum(isinstance(item, FinalAnswer) for item in self.items)
        if final_count > 1:
            raise ValueError("model_response_must_not_have_multiple_final_answers")
        if final_count and any(isinstance(item, ToolCall) for item in self.items):
            raise ValueError("model_response_cannot_mix_tool_calls_and_final_answer")
