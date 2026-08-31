import asyncio

from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder
from runtime.events import EventType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import FakeModelClient, ModelResponse
from runtime.policies import ApprovalDecision, ToolApprovalPolicy
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


def test_file_write_requires_approval_by_default(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient([
        ModelResponse(items=[ToolCall("call_write", "file", {"operation": "write", "path": "blocked.txt", "content": "no"})]),
        ModelResponse(items=[FinalAnswer("Write was blocked.")]),
    ])
    loop = AgentLoop(model, ToolRuntime(registry), ContextBuilder(), RunConfig(trace_root=tmp_path / ".runs"))

    state = asyncio.run(loop.run("try to write"))

    assert not (tmp_path / "blocked.txt").exists()
    assert state.tool_history[0].result["error"] == "approval_required"
    approval_events = [event for event in state.events if event.type == EventType.TOOL_APPROVAL_DECIDED]
    assert approval_events[0].data["decision"] == "require_approval"


def test_preapproved_file_write_reaches_tool_runtime(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient([
        ModelResponse(items=[ToolCall("call_write", "file", {"operation": "write", "path": "allowed.txt", "content": "yes"})]),
        ModelResponse(items=[FinalAnswer("Write completed.")]),
    ])
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(trace_root=tmp_path / ".runs"),
        approval_policy=ToolApprovalPolicy(allow_file_writes=True),
    )

    state = asyncio.run(loop.run("write a file"))

    assert (tmp_path / "allowed.txt").read_text() == "yes"
    assert state.tool_history[0].result["success"] is True


def test_shell_requires_explicit_run_approval() -> None:
    call = ToolCall("call_shell", "shell", {"command": "echo hello"})

    blocked = ToolApprovalPolicy().evaluate(call)
    allowed = ToolApprovalPolicy(allow_shell=True).evaluate(call)

    assert blocked.decision == ApprovalDecision.REQUIRE_APPROVAL
    assert allowed.decision == ApprovalDecision.ALLOW


def test_unclassified_tools_fail_closed() -> None:
    outcome = ToolApprovalPolicy().evaluate(ToolCall("call_new", "future_mutating_tool", {}))

    assert outcome.decision == ApprovalDecision.REQUIRE_APPROVAL
    assert outcome.reason == "unclassified_tool_requires_approval"
