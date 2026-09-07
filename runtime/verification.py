from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .sandbox import CommandRequest, HostRunner, SandboxRunner
from .state import AgentState


@dataclass(frozen=True)
class VerificationResult:
    """An independent verdict about whether the task is actually complete."""

    passed: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskVerifier(Protocol):
    async def verify(
        self,
        task: str,
        candidate_answer: str,
        state: AgentState,
    ) -> VerificationResult:
        """Check observable task outcomes without trusting the model's claim."""


@dataclass(frozen=True)
class FileRequirement:
    path: str
    must_exist: bool = True
    exact_content: str | None = None
    contains: str | None = None


class FileStateVerifier:
    """Verify explicit file-system postconditions inside one workspace."""

    def __init__(self, workspace: Path, requirements: list[FileRequirement]) -> None:
        self.workspace = workspace.resolve()
        self.requirements = tuple(requirements)

    async def verify(
        self,
        task: str,
        candidate_answer: str,
        state: AgentState,
    ) -> VerificationResult:
        failures: list[dict[str, Any]] = []
        for requirement in self.requirements:
            target = (self.workspace / requirement.path).resolve()
            if target != self.workspace and self.workspace not in target.parents:
                failures.append(
                    {"path": requirement.path, "reason": "path_outside_workspace"}
                )
                continue

            exists = target.exists()
            if exists != requirement.must_exist:
                failures.append(
                    {
                        "path": requirement.path,
                        "reason": "unexpected_existence",
                        "expected": requirement.must_exist,
                        "actual": exists,
                    }
                )
                continue
            if not exists or (
                requirement.exact_content is None and requirement.contains is None
            ):
                continue
            if not target.is_file():
                failures.append({"path": requirement.path, "reason": "not_a_file"})
                continue

            content = target.read_text(encoding="utf-8")
            if (
                requirement.exact_content is not None
                and content != requirement.exact_content
            ):
                failures.append(
                    {"path": requirement.path, "reason": "exact_content_mismatch"}
                )
            if requirement.contains is not None and requirement.contains not in content:
                failures.append(
                    {"path": requirement.path, "reason": "required_text_missing"}
                )

        return VerificationResult(
            passed=not failures,
            summary=(
                "All file requirements passed."
                if not failures
                else f"{len(failures)} file requirement(s) failed."
            ),
            details={"failures": failures},
        )


class CommandTaskVerifier:
    """Run an owner-configured test command and require a zero exit code."""

    def __init__(
        self,
        command: str,
        workspace: Path,
        *,
        timeout_seconds: float = 60,
        runner: SandboxRunner | None = None,
    ) -> None:
        self.command = command
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds
        self.runner = runner if runner is not None else HostRunner()

    async def verify(
        self,
        task: str,
        candidate_answer: str,
        state: AgentState,
    ) -> VerificationResult:
        execution = await self.runner.run(
            CommandRequest(
                command=self.command,
                cwd=self.workspace,
                timeout_seconds=self.timeout_seconds,
            )
        )
        passed = not execution.timed_out and execution.exit_code == 0
        return VerificationResult(
            passed=passed,
            summary=(
                "Verification command passed."
                if passed
                else "Verification command failed."
            ),
            details={
                "command": self.command,
                "exit_code": execution.exit_code,
                "stdout": execution.stdout,
                "stderr": execution.stderr,
                "timed_out": execution.timed_out,
                "duration_ms": execution.duration_ms,
                "runner": self.runner.name,
                "isolation": self.runner.isolation.value,
            },
        )


class CompositeTaskVerifier:
    """Require every verifier to pass, preserving every verdict for diagnosis."""

    def __init__(self, verifiers: list[TaskVerifier]) -> None:
        self.verifiers = tuple(verifiers)

    async def verify(
        self,
        task: str,
        candidate_answer: str,
        state: AgentState,
    ) -> VerificationResult:
        results = [
            await verifier.verify(task, candidate_answer, state)
            for verifier in self.verifiers
        ]
        passed = all(result.passed for result in results)
        return VerificationResult(
            passed=passed,
            summary=(
                "All task verifiers passed."
                if passed
                else f"{sum(not result.passed for result in results)} verifier(s) failed."
            ),
            details={"results": [result.to_dict() for result in results]},
        )
