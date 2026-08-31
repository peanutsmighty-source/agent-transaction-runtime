import asyncio

from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder
from runtime.events import EventType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import FakeModelClient, ModelResponse
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


def test_large_tool_result_is_truncated_only_in_model_context(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    (tmp_path / "large.txt").write_text("x" * 1_000, encoding="utf-8")
    model = FakeModelClient([
        ModelResponse(items=[ToolCall("call_read", "file", {"operation": "read", "path": "large.txt"})]),
        ModelResponse(items=[FinalAnswer("Read the file.")]),
    ])
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(max_steps=3, trace_root=tmp_path / ".runs", max_tool_output_chars=100),
    )

    state = asyncio.run(loop.run("read a large file"))

    assert len(state.tool_history[0].result["output"]["content"]) == 1_000
    assert "TRUNCATED TOOL OUTPUT" in state.messages[-1].content
    event_types = [event.type for event in state.events]
    assert EventType.TOOL_OUTPUT_TRUNCATED in event_types
