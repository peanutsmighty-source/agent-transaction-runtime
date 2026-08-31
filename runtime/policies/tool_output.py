from __future__ import annotations

import json
from dataclasses import dataclass

from ..tools.base import ToolResult


@dataclass(frozen=True)
class RenderedToolOutput:
    content: str
    original_tokens: int
    retained_tokens: int
    truncated_tokens: int

    @property
    def truncated(self) -> bool:
        return self.truncated_tokens > 0


class ToolOutputPolicy:
    """Keeps complete tool results in trace while bounding what is sent back to the model."""

    def __init__(self, max_chars_per_result: int = 4_000) -> None:
        if max_chars_per_result < 64:
            raise ValueError("max_chars_per_result_must_be_at_least_64")
        self.max_chars_per_result = max_chars_per_result

    def render_for_context(self, result: ToolResult) -> RenderedToolOutput:
        serialized = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        original_tokens = self._estimate_tokens(serialized)
        if len(serialized) <= self.max_chars_per_result:
            return RenderedToolOutput(serialized, original_tokens, original_tokens, 0)

        head_chars = self.max_chars_per_result // 2
        tail_chars = self.max_chars_per_result - head_chars
        preview = f"{serialized[:head_chars]}\n[TRUNCATED TOOL OUTPUT]\n{serialized[-tail_chars:]}"
        context_value = json.dumps(
            {
                "success": result.success,
                "error": result.error,
                "output_preview": preview,
                "original_chars": len(serialized),
                "retained_preview_chars": len(preview),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        retained_tokens = self._estimate_tokens(context_value)
        return RenderedToolOutput(context_value, original_tokens, retained_tokens, max(0, original_tokens - retained_tokens))

    @staticmethod
    def _estimate_tokens(content: str) -> int:
        return max(1, (len(content) + 3) // 4) if content else 0
