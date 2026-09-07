from __future__ import annotations

import asyncio
from pathlib import Path

from runtime import (
    CommandTaskVerifier,
    CompositeTaskVerifier,
    FileRequirement,
    FileStateVerifier,
    VerificationResult,
)
from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder, ContextItemType
from runtime.events import EventType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import FakeModelClient, ModelResponse
from runtime.policies import ToolApprovalPolicy
from runtime.sandbox import CommandExecution, IsolationKind
from runtime.state import AgentState, AgentStatus
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


class ScriptedVerifier:
    def __init__(self, results: list[VerificationResult]) -> None:
        self.results = list(results)
        self.candidates: list[str] = []

    async def verify(
        self,
        task: str,
        candidate_answer: str,
        state: AgentState,
    ) -> VerificationResult:
        self.candidates.append(candidate_answer)
        return self.results.pop(0)


class FakeRunner:
    name = "fake"
    isolation = IsolationKind.NONE

    def __init__(self, execution: CommandExecution) -> None:
        self.execution = execution
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return self.execution


def test_failed_candidate_is_fed_back_and_loop_can_repair(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient(
        [
            ModelResponse(items=[FinalAnswer("Done before changing anything.")]),
            ModelResponse(
                items=[
                    ToolCall(
                        "call_fix",
                        "file",
                        {
                            "operation": "write",
                            "path": "answer.txt",
                            "content": "42",
                        },
                    )
                ]
            ),
            ModelResponse(items=[FinalAnswer("Now the file is fixed.")]),
        ]
    )
    verifier = FileStateVerifier(
        tmp_path,
        [FileRequirement("answer.txt", exact_content="42")],
    )
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(max_steps=4, trace_root=tmp_path / ".runs"),
        approval_policy=ToolApprovalPolicy(allow_file_writes=True),
        task_verifier=verifier,
    )

    state = asyncio.run(loop.run("write 42 to answer.txt"))

    assert state.status == AgentStatus.COMPLETED
    assert state.final_answer == "Now the file is fixed."
    assert [attempt.passed for attempt in state.verification_history] == [False, True]
    assert model.requests[1].items[-2].type == ContextItemType.ASSISTANT
    assert model.requests[1].items[-1].type == ContextItemType.VERIFICATION
    assert "unexpected_existence" in model.requests[1].items[-1].content
    result_events = [
        event
        for event in state.events
        if event.type == EventType.TASK_VERIFICATION_RESULT
    ]
    assert [event.data["passed"] for event in result_events] == [False, True]


def test_command_verifier_uses_exit_code_as_verdict(tmp_path: Path) -> None:
    runner = FakeRunner(
        CommandExecution(
            exit_code=1,
            stdout="1 failed",
            stderr="assertion error",
            duration_ms=12,
        )
    )
    verifier = CommandTaskVerifier("python -m pytest -q", tmp_path, runner=runner)

    result = asyncio.run(verifier.verify("fix tests", "done", AgentState("fix tests")))

    assert result.passed is False
    assert result.details["exit_code"] == 1
    assert result.details["stderr"] == "assertion error"
    assert runner.requests[0].cwd == tmp_path.resolve()


def test_composite_verifier_requires_every_contract_to_pass(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_text("42", encoding="utf-8")
    passing_file_check = FileStateVerifier(
        tmp_path,
        [FileRequirement("answer.txt", exact_content="42")],
    )
    failing_assertion = ScriptedVerifier(
        [VerificationResult(False, "Expected two files.", {"actual": 1})]
    )
    verifier = CompositeTaskVerifier([passing_file_check, failing_assertion])

    result = asyncio.run(verifier.verify("make files", "done", AgentState("make files")))

    assert result.passed is False
    assert result.summary == "1 verifier(s) failed."
    assert [item["passed"] for item in result.details["results"]] == [True, False]


def test_file_verifier_rejects_requirements_outside_workspace(tmp_path: Path) -> None:
    verifier = FileStateVerifier(tmp_path, [FileRequirement("../secret.txt")])

    result = asyncio.run(verifier.verify("read", "done", AgentState("read")))

    assert result.passed is False
    assert result.details["failures"][0]["reason"] == "path_outside_workspace"
