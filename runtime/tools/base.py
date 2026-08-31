from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass
class ToolResult:
    success: bool
    output: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tool(Protocol):
    name: str
    description: str

    def schema(self) -> dict[str, Any]: ...

    async def execute(self, arguments: dict[str, Any]) -> ToolResult: ...
