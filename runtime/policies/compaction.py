from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from ..context import (
    ContextItem,
    ContextItemType,
    ModelContext,
    estimate_tokens,
    group_context_units,
)
from ..summarization import Summarizer, SummaryRequest


@dataclass(frozen=True)
class CompactionResult:
    strategy: str
    context: ModelContext
    original_tokens: int
    compacted_tokens: int
    target_tokens: int
    removed_item_ids: list[str]
    summarized_item_ids: list[str] | None = None
    retained_item_ids: list[str] | None = None
    summary_item_id: str | None = None
    error: str | None = None
    error_detail: str | None = None
    fallback_strategy: str | None = None

    @property
    def applied(self) -> bool:
        return bool(self.removed_item_ids)

    def to_event_data(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "original_tokens": self.original_tokens,
            "compacted_tokens": self.compacted_tokens,
            "target_tokens": self.target_tokens,
            "removed_tokens": self.original_tokens - self.compacted_tokens,
            "removed_item_ids": self.removed_item_ids,
            "summarized_item_ids": self.summarized_item_ids or [],
            "retained_item_ids": self.retained_item_ids or [],
            "summary_item_id": self.summary_item_id,
            "error": self.error,
            "error_detail": self.error_detail,
            "fallback_strategy": self.fallback_strategy,
        }


class CompactionPolicy(Protocol):
    name: str

    async def compact(self, context: ModelContext, target_tokens: int) -> CompactionResult: ...


class SlidingWindowCompaction:
    """Keep system/task plus the largest recent contiguous unit suffix that fits."""

    name = "sliding_window"

    def __init__(self, min_recent_units: int = 2) -> None:
        if min_recent_units < 0:
            raise ValueError("min_recent_units_must_not_be_negative")
        self.min_recent_units = min_recent_units

    async def compact(self, context: ModelContext, target_tokens: int) -> CompactionResult:
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
            retained_item_ids=[item.id for item in retained_history],
        )


class FullSummaryCompaction:
    """Replace older complete context units with one derived summary item."""

    name = "full_summary"

    def __init__(
        self,
        summarizer: Summarizer,
        min_recent_units: int = 2,
        fallback: CompactionPolicy | None = None,
    ) -> None:
        if min_recent_units < 0:
            raise ValueError("min_recent_units_must_not_be_negative")
        self.summarizer = summarizer
        self.min_recent_units = min_recent_units
        self.fallback = fallback or SlidingWindowCompaction(min_recent_units)

    async def compact(self, context: ModelContext, target_tokens: int) -> CompactionResult:
        if target_tokens <= 0:
            raise ValueError("compaction_target_tokens_must_be_positive")
        if context.estimated_tokens <= target_tokens:
            return CompactionResult(
                strategy=self.name,
                context=context,
                original_tokens=context.estimated_tokens,
                compacted_tokens=context.estimated_tokens,
                target_tokens=target_tokens,
                removed_item_ids=[],
            )

        mandatory = [
            item
            for item in context.items
            if item.type in (ContextItemType.SYSTEM, ContextItemType.TASK)
        ]
        history = [item for item in context.items if item not in mandatory]
        units = group_context_units(history)
        recent_count = min(self.min_recent_units, len(units))
        recent_units = units[len(units) - recent_count :] if recent_count else []
        older_units = units[: len(units) - recent_count] if recent_count else units
        recent_items = [item for unit in recent_units for item in unit.items]
        older_items = [item for unit in older_units for item in unit.items]

        summary_budget = target_tokens - sum(
            item.token_count for item in mandatory + recent_items
        )
        if not older_items:
            return await self._fallback(context, target_tokens, "no_summarizable_history")
        if summary_budget <= 0:
            return await self._fallback(context, target_tokens, "summary_has_no_token_budget")

        request = SummaryRequest(items=tuple(older_items), max_tokens=summary_budget)
        try:
            summary = (await self.summarizer.summarize(request)).strip()
        except Exception as error:
            return await self._fallback(
                context,
                target_tokens,
                "summarizer_failed",
                f"{type(error).__name__}: {error}",
            )
        if not summary:
            return await self._fallback(context, target_tokens, "summarizer_returned_empty_summary")

        summary_tokens = estimate_tokens(summary)
        if summary_tokens > summary_budget:
            return await self._fallback(
                context,
                target_tokens,
                "summary_exceeds_budget",
                f"summary_tokens={summary_tokens}, summary_budget={summary_budget}",
            )

        summary_item = ContextItem(
            id="summary",
            type=ContextItemType.SUMMARY,
            content=summary,
            token_count=summary_tokens,
            name="full_summary",
        )
        compacted = ModelContext(items=mandatory + [summary_item] + recent_items)
        return CompactionResult(
            strategy=self.name,
            context=compacted,
            original_tokens=context.estimated_tokens,
            compacted_tokens=compacted.estimated_tokens,
            target_tokens=target_tokens,
            removed_item_ids=[item.id for item in older_items],
            summarized_item_ids=[item.id for item in older_items],
            retained_item_ids=[item.id for item in recent_items],
            summary_item_id=summary_item.id,
        )

    async def _fallback(
        self,
        context: ModelContext,
        target_tokens: int,
        error: str,
        error_detail: str | None = None,
    ) -> CompactionResult:
        fallback_result = await self.fallback.compact(context, target_tokens)
        return replace(
            fallback_result,
            strategy=self.name,
            error=error,
            error_detail=error_detail,
            fallback_strategy=fallback_result.strategy,
        )
