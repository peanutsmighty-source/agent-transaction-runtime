from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

from runtime.actions import AssistantMessage, FinalAnswer, ReasoningSummary, ToolCall
from runtime.context import ContextBuilder
from runtime.events import EventType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import FakeModelClient, ModelResponse
from runtime.policies import ToolApprovalPolicy
from runtime.sandbox import CommandExecution, IsolationKind
from runtime.state import AgentStatus
from runtime.tools import FileTool, ShellTool, ToolRegistry, ToolRuntime


class SequenceRunner:
    name = "sequence"
    isolation = IsolationKind.NONE

    def __init__(self, executions: list[CommandExecution]) -> None:
        self.executions = deque(executions)

    async def run(self, request):
        return self.executions.popleft()


def make_loop(tmp_path: Path, model: FakeModelClient, registry: ToolRegistry, **kwargs):
    return AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(max_steps=6, trace_root=tmp_path / ".runs"),
        **kwargs,
    )


def test_multiple_tool_calls_keep_order_and_pair_results_after_partial_failure(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient(
        [
            ModelResponse(
                items=[
                    ToolCall(
                        "call_missing",
                        "file",
                        {"operation": "read", "path": "missing.txt"},
                    ),
                    ToolCall(
                        "call_list",
                        "file",
                        {"operation": "list", "path": "."},
                    ),
                ]
            ),
            ModelResponse(items=[FinalAnswer("Observed both results.")]),
        ]
    )

    state = asyncio.run(make_loop(tmp_path, model, registry).run("inspect files"))

    assert state.status == AgentStatus.COMPLETED
    assert [execution.call_id for execution in state.tool_history] == [
        "call_missing",
        "call_list",
    ]
    assert [execution.result["success"] for execution in state.tool_history] == [
        False,
        True,
    ]
    assert [message.tool_call_id for message in state.messages] == [
        "call_missing",
        "call_list",
        "call_missing",
        "call_list",
    ]


def test_model_can_correct_invalid_tool_arguments_on_next_turn(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient(
        [
            ModelResponse(
                items=[
                    ToolCall(
                        "call_bad",
                        "file",
                        {"operation": "write", "path": "fixed.txt"},
                    )
                ]
            ),
            ModelResponse(
                items=[
                    ToolCall(
                        "call_corrected",
                        "file",
                        {
                            "operation": "write",
                            "path": "fixed.txt",
                            "content": "fixed",
                        },
                    )
                ]
            ),
            ModelResponse(items=[FinalAnswer("Corrected the arguments.")]),
        ]
    )

    state = asyncio.run(
        make_loop(
            tmp_path,
            model,
            registry,
            approval_policy=ToolApprovalPolicy(allow_file_writes=True),
        ).run("write fixed.txt")
    )

    assert state.status == AgentStatus.COMPLETED
    assert [execution.result["success"] for execution in state.tool_history] == [
        False,
        True,
    ]
    assert state.tool_history[0].result["error"] == "'content'"
    assert (tmp_path / "fixed.txt").read_text(encoding="utf-8") == "fixed"


def test_shell_non_zero_and_timeout_are_observations_the_model_can_recover_from(
    tmp_path: Path,
) -> None:
    runner = SequenceRunner(
        [
            CommandExecution(1, "", "tests failed", 5),
            CommandExecution(None, "", "", 100, timed_out=True),
            CommandExecution(0, "tests passed", "", 7),
        ]
    )
    registry = ToolRegistry()
    registry.register(ShellTool(tmp_path, runner=runner))
    model = FakeModelClient(
        [
            ModelResponse(items=[ToolCall("call_fail", "shell", {"command": "test"})]),
            ModelResponse(items=[ToolCall("call_timeout", "shell", {"command": "test"})]),
            ModelResponse(items=[ToolCall("call_pass", "shell", {"command": "test"})]),
            ModelResponse(items=[FinalAnswer("Tests now pass.")]),
        ]
    )

    state = asyncio.run(
        make_loop(
            tmp_path,
            model,
            registry,
            approval_policy=ToolApprovalPolicy(allow_shell=True),
        ).run("make tests pass")
    )

    assert state.status == AgentStatus.COMPLETED
    assert [execution.result["error"] for execution in state.tool_history] == [
        "shell_non_zero_exit",
        "tool_timeout",
        None,
    ]


def test_mixed_assistant_reasoning_and_tool_items_preserve_response_order(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient(
        [
            ModelResponse(
                items=[
                    AssistantMessage("I will inspect the workspace."),
                    ReasoningSummary("Need a directory listing."),
                    ToolCall("call_list", "file", {"operation": "list", "path": "."}),
                ]
            ),
            ModelResponse(items=[FinalAnswer("Inspection complete.")]),
        ]
    )

    state = asyncio.run(make_loop(tmp_path, model, registry).run("inspect"))

    response_items = [
        event.data["item_type"]
        for event in state.events
        if event.type == EventType.MODEL_RESPONSE_ITEM
    ]
    assert response_items == [
        "AssistantMessage",
        "ReasoningSummary",
        "ToolCall",
        "FinalAnswer",
    ]
    assert [(message.role, message.name) for message in state.messages[:2]] == [
        ("assistant", None),
        ("assistant", "reasoning_summary"),
    ]


def test_denied_write_and_shell_are_returned_as_recoverable_observations(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    registry.register(ShellTool(tmp_path))
    model = FakeModelClient(
        [
            ModelResponse(
                items=[
                    ToolCall(
                        "call_write",
                        "file",
                        {"operation": "write", "path": "blocked.txt", "content": "x"},
                    ),
                    ToolCall("call_shell", "shell", {"command": "echo blocked"}),
                ]
            ),
            ModelResponse(items=[FinalAnswer("Both operations require approval.")]),
        ]
    )

    state = asyncio.run(make_loop(tmp_path, model, registry).run("try blocked actions"))

    assert state.status == AgentStatus.COMPLETED
    assert not (tmp_path / "blocked.txt").exists()
    assert [execution.result["error"] for execution in state.tool_history] == [
        "approval_required",
        "approval_required",
    ]
    assert "approval_required" in model.requests[1].items[-1].content
    decisions = [
        event.data["decision"]
        for event in state.events
        if event.type == EventType.TOOL_APPROVAL_DECIDED
    ]
    assert decisions == ["require_approval", "require_approval"]


def test_no_final_answer_fails_when_scripted_model_is_exhausted(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient(
        [
            ModelResponse(
                items=[ToolCall("call_list", "file", {"operation": "list", "path": "."})]
            )
        ]
    )

    state = asyncio.run(make_loop(tmp_path, model, registry).run("never finish"))

    assert state.status == AgentStatus.FAILED
    assert state.error == "fake_model_responses_exhausted"
    assert EventType.RUNTIME_ERROR in [event.type for event in state.events]
