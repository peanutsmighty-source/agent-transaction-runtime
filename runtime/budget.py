from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BudgetDecision:
    current_tokens: int
    effective_input_tokens: int
    compact_trigger_tokens: int
    should_compact: bool

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


class ContextBudget:
    """Pure policy for deciding when the current model input needs compaction."""

    def __init__(self, max_tokens: int = 128_000, reserved_output_tokens: int = 16_000, compact_threshold: float = 0.75) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens_must_be_positive")
        if not 0 <= reserved_output_tokens < max_tokens:
            raise ValueError("reserved_output_tokens_must_be_between_zero_and_max_tokens")
        if not 0 < compact_threshold <= 1:
            raise ValueError("compact_threshold_must_be_between_zero_and_one")
        self.max_tokens = max_tokens
        self.reserved_output_tokens = reserved_output_tokens
        self.compact_threshold = compact_threshold

    @property
    def effective_input_tokens(self) -> int:
        return self.max_tokens - self.reserved_output_tokens

    @property
    def compact_trigger_tokens(self) -> int:
        return int(self.effective_input_tokens * self.compact_threshold)

    def evaluate(self, current_tokens: int) -> BudgetDecision:
        return BudgetDecision(
            current_tokens=current_tokens,
            effective_input_tokens=self.effective_input_tokens,
            compact_trigger_tokens=self.compact_trigger_tokens,
            should_compact=current_tokens >= self.compact_trigger_tokens,
        )
