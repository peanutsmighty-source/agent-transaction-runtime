from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ToolResult


class FileTool:
    name = "file"
    description = "Read, write, or list files inside the configured workspace."

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["read", "write", "list"]},
                    "path": {"type": "string", "description": "Path relative to the workspace."},
                    "content": {"type": "string", "description": "Required when operation is write."},
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        }

    def _path(self, raw_path: str) -> Path:
        candidate = (self.workspace / raw_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError("path_outside_workspace")
        return candidate

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            operation = arguments["operation"]
            path = self._path(arguments.get("path", "."))
            if operation == "read":
                return ToolResult(True, {"path": str(path.relative_to(self.workspace)), "content": path.read_text(encoding="utf-8")})
            if operation == "write":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(arguments["content"], encoding="utf-8")
                return ToolResult(True, {"path": str(path.relative_to(self.workspace)), "bytes_written": len(arguments["content"].encode())})
            if operation == "list":
                entries = sorted(item.name for item in path.iterdir())
                return ToolResult(True, {"path": str(path.relative_to(self.workspace)), "entries": entries})
            return ToolResult(False, {}, f"unknown_file_operation: {operation}")
        except (KeyError, OSError, ValueError) as error:
            return ToolResult(False, {}, str(error))
