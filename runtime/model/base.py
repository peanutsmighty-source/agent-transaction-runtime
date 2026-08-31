from __future__ import annotations

from typing import Protocol

from ..context import ModelContext
from .response import ModelResponse


class ModelClient(Protocol):
    async def generate(self, context: ModelContext, tools: list[dict]) -> ModelResponse:
        """Return the structured items produced by one model turn."""
