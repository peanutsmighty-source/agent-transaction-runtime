from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class IsolationKind(StrEnum):
    """The technical boundary enforced by a command runner."""

    NONE = "none"
    OS = "os"
    CONTAINER = "container"
    VM = "vm"


@dataclass(frozen=True)
class CommandRequest:
    command: str
    cwd: Path
    timeout_seconds: float


@dataclass(frozen=True)
class CommandExecution:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class SandboxRunner(Protocol):
    """Execution boundary used by ShellTool.

    Implementations must declare their isolation honestly. A runner with
    IsolationKind.NONE is useful for development, but is not a sandbox.
    """

    name: str
    isolation: IsolationKind

    async def run(self, request: CommandRequest) -> CommandExecution: ...
