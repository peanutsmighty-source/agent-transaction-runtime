from __future__ import annotations

from collections import defaultdict

from .context import ModelContext
from .state import ContextItemStat, ContextStats


class ContextObserver:
    """Measures a built context; it does not decide what should be retained."""

    def __init__(self, context_window_tokens: int = 128_000, largest_item_limit: int = 5) -> None:
        self.context_window_tokens = context_window_tokens
        self.largest_item_limit = largest_item_limit

    def inspect(self, context: ModelContext) -> ContextStats:
        by_type: defaultdict[str, int] = defaultdict(int)
        for item in context.items:
            by_type[item.type.value] += item.token_count
        largest = sorted(context.items, key=lambda item: item.token_count, reverse=True)[: self.largest_item_limit]
        return ContextStats(
            item_count=len(context.items),
            estimated_tokens=context.estimated_tokens,
            tokens_by_type=dict(by_type),
            largest_items=[ContextItemStat(id=item.id, type=item.type.value, token_count=item.token_count) for item in largest],
        )

    def format(self, stats: ContextStats) -> str:
        usage = stats.estimated_tokens / self.context_window_tokens if self.context_window_tokens else 0
        lines = ["Context Inspector", "-----------------", f"Total: {stats.estimated_tokens} / {self.context_window_tokens} tokens ({usage:.1%})", "By type:"]
        lines.extend(f"  {type}: {tokens}" for type, tokens in sorted(stats.tokens_by_type.items()))
        lines.append("Largest items:")
        lines.extend(f"  {item.id} ({item.type}): {item.token_count}" for item in stats.largest_items)
        return "\n".join(lines)
