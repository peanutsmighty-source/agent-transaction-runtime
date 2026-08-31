from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..context import ContextItemType, ModelContext, group_context_units


@dataclass(frozen=True)
class CompactionResult:
    strategy: str
    context: ModelContext
    original_tokens: int
    compacted_tokens: int
    target_tokens: int
    removed_item_ids: list[str]

    @property
    def applied(self) -> bool:
        return bool(self.removed_item_ids)

    def to_event_data(self) -> dict[str, str | int | list[str]]:
        return {
            "strategy": self.strategy,
            "original_tokens": self.original_tokens,
            "compacted_tokens": self.compacted_tokens,
            "target_tokens": self.target_tokens,
            "removed_tokens": self.original_tokens - self.compacted_tokens,
            "removed_item_ids": self.removed_item_ids,
        }


class CompactionPolicy(Protocol):
    name: str

    def compact(self, context: ModelContext, target_tokens: int) -> CompactionResult: ...


class SlidingWindowCompaction:
    """Keep system/task plus the largest recent contiguous unit suffix that fits."""

    name = "sliding_window"

    def __init__(self, min_recent_units: int = 2) -> None:
        if min_recent_units < 0:
            raise ValueError("min_recent_units_must_not_be_negative")
        self.min_recent_units = min_recent_units

    def compact(self, context: ModelContext, target_tokens: int) -> CompactionResult:
        if target_tokens <= 0:
            raise ValueError("compaction_target_tokens_must_be_positive")

        mandatory = [
            item
            for item in context.items
            if item.type in (ContextItemType.SYSTEM, ContextItemType.TASK)
        ]
        history = [item for item in context.items if item not in mandatory]
        units = group_context_units(history)
        recent_count = min(self.min_recent_units, len(units))
        retained_units = units[len(units) - recent_count :] if recent_count else []
        retained_tokens = sum(item.token_count for item in mandatory) + sum(
            unit.token_count for unit in retained_units
        )

        older = units[: len(units) - recent_count] if recent_count else units
        for unit in reversed(older):
            if retained_tokens + unit.token_count > target_tokens:
                break
            retained_units.insert(0, unit)
            retained_tokens += unit.token_count

        retained_history = [item for unit in retained_units for item in unit.items]
        retained_ids = {item.id for item in mandatory + retained_history}
        removed_ids = [item.id for item in context.items if item.id not in retained_ids]
        compacted = ModelContext(items=mandatory + retained_history)
        return CompactionResult(
            strategy=self.name,
            context=compacted,
            original_tokens=context.estimated_tokens,
            compacted_tokens=compacted.estimated_tokens,
            target_tokens=target_tokens,
            removed_item_ids=removed_ids,
        )
