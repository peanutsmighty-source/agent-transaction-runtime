from __future__ import annotations

from ..actions import ToolCall
from .base import ToolResult
from .registry import ToolRegistry


class ToolRuntime:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            tool = self.registry.get(call.name)
        except ValueError as error:
            # Unknown tools are observations the model can recover from, not runtime crashes.
            return ToolResult(success=False, output={}, error=str(error))
        return await tool.execute(call.arguments)
