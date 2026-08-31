from __future__ import annotations

from pathlib import Path
from typing import Any

from ..sandbox import CommandRequest, HostRunner, SandboxRunner
from .base import ToolResult


class ShellTool:
    name = "shell"
    description = "Run a shell command starting in the configured workspace. Isolation depends on the command runner."

    def __init__(
        self,
        workspace: Path,
        timeout_seconds: float = 15,
        output_limit: int = 12_000,
        runner: SandboxRunner | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.runner = runner if runner is not None else HostRunner()

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": {"command": "shell command", "cwd": "optional relative directory"}}

    def _cwd(self, raw_cwd: str) -> Path:
        cwd = (self.workspace / raw_cwd).resolve()
        if cwd != self.workspace and self.workspace not in cwd.parents:
            raise ValueError("cwd_outside_workspace")
        return cwd

    def _truncate(self, content: str) -> tuple[str, bool]:
        if len(content) <= self.output_limit:
            return content, False
        head = self.output_limit // 2
        tail = self.output_limit - head
        return f"{content[:head]}\n[TRUNCATED]\n{content[-tail:]}", True

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            command = arguments["command"]
            cwd = self._cwd(arguments.get("cwd", "."))
        except (KeyError, ValueError) as error:
            return ToolResult(False, {}, str(error))

        try:
            execution = await self.runner.run(
                CommandRequest(
                    command=command,
                    cwd=cwd,
                    timeout_seconds=self.timeout_seconds,
                )
            )
        except OSError as error:
            return ToolResult(
                False,
                {
                    "runner": self.runner.name,
                    "isolation": self.runner.isolation.value,
                    "launch_error": str(error),
                },
                "shell_launch_failed",
            )

        execution_metadata = {
            "runner": self.runner.name,
            "isolation": self.runner.isolation.value,
            "duration_ms": execution.duration_ms,
        }
        if execution.timed_out:
            return ToolResult(False, execution_metadata, "tool_timeout")

        stdout_text, stdout_truncated = self._truncate(execution.stdout)
        stderr_text, stderr_truncated = self._truncate(execution.stderr)
        return ToolResult(
            success=execution.exit_code == 0,
            output={
                **execution_metadata,
                "exit_code": execution.exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
            error=None if execution.exit_code == 0 else "shell_non_zero_exit",
        )
