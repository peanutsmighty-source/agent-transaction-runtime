from __future__ import annotations

import asyncio
from pathlib import Path

from runtime.actions import AssistantMessage, FinalAnswer, ToolCall
from runtime.context import ContextBuilder
from runtime.loop import AgentLoop, RunConfig
from runtime.model import FakeModelClient, ModelResponse, ModelUsage
from runtime.policies import SlidingWindowCompaction, ToolApprovalPolicy
from runtime.state import AgentStatus
from runtime.events import EventType
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


def test_loop_records_tool_execution_and_final_answer(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient([
        ModelResponse(
            response_id="response_1",
            items=[ToolCall("call_write", "file", {"operation": "write", "path": "answer.txt", "content": "42"})],
            usage=ModelUsage(input_tokens=100, output_tokens=20),
        ),
        ModelResponse(response_id="response_2", items=[FinalAnswer("Fixed the task.")], usage=ModelUsage(input_tokens=120, output_tokens=10)),
    ])
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(max_steps=3, trace_root=tmp_path / ".runs"),
        approval_policy=ToolApprovalPolicy(allow_file_writes=True),
    )

    state = asyncio.run(loop.run("write the answer"))

    assert state.status == AgentStatus.COMPLETED
    assert state.final_answer == "Fixed the task."
    assert (tmp_path / "answer.txt").read_text() == "42"
    assert state.tool_history[0].result["success"] is True
    assert state.tool_history[0].call_id == "call_write"
    assert state.messages[-1].tool_call_id == "call_write"
    assert state.step == 2
    assert state.model_usage.input_tokens == 220
    assert state.context_stats.tokens_by_type["tool_result"] > 0
    assert (tmp_path / ".runs").glob("*/events.jsonl")


def test_loop_stops_when_max_steps_is_exceeded(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient([ModelResponse(items=[ToolCall("call_list", "file", {"operation": "list", "path": "."})])])
    loop = AgentLoop(model, ToolRuntime(registry), ContextBuilder(), RunConfig(max_steps=1, trace_root=tmp_path / ".runs"))

    state = asyncio.run(loop.run("keep working"))

    assert state.status == AgentStatus.FAILED
    assert state.error == "max_steps_exceeded"


def test_loop_emits_warning_for_repeated_failed_tool_calls(tmp_path: Path) -> None:
    registry = ToolRegistry()
    model = FakeModelClient([
        ModelResponse(items=[ToolCall("call_1", "missing", {"path": "same"})]),
        ModelResponse(items=[ToolCall("call_2", "missing", {"path": "same"})]),
        ModelResponse(items=[ToolCall("call_3", "missing", {"path": "same"})]),
        ModelResponse(items=[FinalAnswer("I cannot use that tool.")]),
    ])
    loop = AgentLoop(model, ToolRuntime(registry), ContextBuilder(), RunConfig(max_steps=5, trace_root=tmp_path / ".runs"))

    state = asyncio.run(loop.run("try a missing tool"))

    warnings = [event for event in state.events if event.type == EventType.POSSIBLE_LOOP_DETECTED]
    assert state.status == AgentStatus.COMPLETED
    assert len(warnings) == 1
    assert warnings[0].data["repetitions"] == 3


def test_loop_emits_compaction_required_when_context_budget_is_reached(tmp_path: Path) -> None:
    registry = ToolRegistry()
    model = FakeModelClient([ModelResponse(items=[FinalAnswer("Done")])])
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder("x" * 100),
        RunConfig(
            max_steps=1,
            trace_root=tmp_path / ".runs",
            context_window_tokens=100,
            reserved_output_tokens=20,
            compact_threshold=0.5,
        ),
    )

    state = asyncio.run(loop.run("y" * 100))

    assert state.context_budget.should_compact is True
    assert EventType.CONTEXT_COMPACTION_REQUIRED in [event.type for event in state.events]


def test_one_model_turn_can_execute_multiple_tool_calls_in_order(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = FakeModelClient([
        ModelResponse(
            response_id="response_tools",
            items=[
                ToolCall("call_a", "file", {"operation": "write", "path": "a.txt", "content": "A"}),
                ToolCall("call_b", "file", {"operation": "write", "path": "b.txt", "content": "B"}),
            ],
        ),
        ModelResponse(items=[FinalAnswer("Both files written.")]),
    ])
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(max_steps=3, trace_root=tmp_path / ".runs"),
        approval_policy=ToolApprovalPolicy(allow_file_writes=True),
    )

    state = asyncio.run(loop.run("write two files"))

    assert state.status == AgentStatus.COMPLETED
    assert state.step == 2
    assert [execution.call_id for execution in state.tool_history] == ["call_a", "call_b"]
    assert (tmp_path / "a.txt").read_text() == "A"
    assert (tmp_path / "b.txt").read_text() == "B"


def test_loop_compacts_model_view_without_deleting_state_history(tmp_path: Path) -> None:
    registry = ToolRegistry()
    model = FakeModelClient([
        ModelResponse(items=[AssistantMessage(character * 80) for character in "abcdef"]),
        ModelResponse(items=[FinalAnswer("History compacted.")]),
    ])
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder("short system"),
        RunConfig(
            max_steps=3,
            trace_root=tmp_path / ".runs",
            context_window_tokens=200,
            reserved_output_tokens=20,
            compact_threshold=0.5,
            compaction_target_ratio=0.5,
        ),
        compaction_policy=SlidingWindowCompaction(min_recent_units=2),
    )

    state = asyncio.run(loop.run("short task"))

    compactions = [
        event for event in state.events
        if event.type == EventType.CONTEXT_COMPACTION_APPLIED
    ]
    assert state.status == AgentStatus.COMPLETED
    assert len(state.messages) == 6
    assert len(model.requests[1].items) < 8
    assert len(compactions) == 1
    assert compactions[0].data["removed_tokens"] > 0
    assert state.summary()["compactions"] == 1
