from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from runtime.actions import ToolCall
from runtime.context import ContextBuilder
from runtime.events import EventType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import ModelResponse, ResponsesModelClient
from runtime.policies import ToolApprovalPolicy
from runtime.sandbox import CommandExecution, CommandRequest, HostRunner, IsolationKind
from runtime.state import AgentState, AgentStatus
from runtime.tools import ShellTool, ToolRegistry, ToolRuntime


class BlockingModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def generate(self, context, tools):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class OneToolModel:
    def __init__(self) -> None:
        self.used = False

    async def generate(self, context, tools):
        if self.used:
            raise AssertionError("model should be cancelled during the first tool")
        self.used = True
        return ModelResponse(items=[ToolCall("call_shell", "shell", {"command": "wait"})])


class BlockingRunner:
    name = "blocking"
    isolation = IsolationKind.NONE

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cleaned_up = False

    async def run(self, request):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cleaned_up = True
            raise
        return CommandExecution(0, "", "", 0)


def run_directory(trace_root: Path) -> Path:
    return next(trace_root.iterdir())


def test_cancelling_during_model_call_returns_cancelled_state_and_trace(
    tmp_path: Path,
) -> None:
    async def scenario():
        model = BlockingModel()
        trace_root = tmp_path / ".runs"
        loop = AgentLoop(
            model,
            ToolRuntime(ToolRegistry()),
            ContextBuilder(),
            RunConfig(trace_root=trace_root),
        )
        task = asyncio.create_task(loop.run("wait for model"))
        await model.started.wait()
        task.cancel()
        return await task, model, trace_root

    state, model, trace_root = asyncio.run(scenario())

    assert state.status == AgentStatus.CANCELLED
    assert state.error == "cancelled_by_caller"
    assert model.cancelled is True
    assert EventType.AGENT_CANCELLED in [event.type for event in state.events]
    result = json.loads(
        (run_directory(trace_root) / "result.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "cancelled"


def test_cancelling_during_tool_call_propagates_cleanup_before_loop_stops(
    tmp_path: Path,
) -> None:
    async def scenario():
        runner = BlockingRunner()
        registry = ToolRegistry()
        registry.register(ShellTool(tmp_path, runner=runner))
        loop = AgentLoop(
            OneToolModel(),
            ToolRuntime(registry),
            ContextBuilder(),
            RunConfig(trace_root=tmp_path / ".runs"),
            approval_policy=ToolApprovalPolicy(allow_shell=True),
        )
        task = asyncio.create_task(loop.run("run a blocking tool"))
        await runner.started.wait()
        task.cancel()
        return await task, runner

    state, runner = asyncio.run(scenario())

    assert runner.cleaned_up is True
    assert state.status == AgentStatus.CANCELLED
    assert len(state.tool_history) == 0


class BlockingStream:
    def __init__(self) -> None:
        self.iteration_started = asyncio.Event()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.iteration_started.set()
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class FakeResponsesEndpoint:
    def __init__(self, stream: BlockingStream) -> None:
        self.stream = stream

    async def create(self, **kwargs):
        return self.stream


class FakeSDKClient:
    def __init__(self, stream: BlockingStream) -> None:
        self.responses = FakeResponsesEndpoint(stream)


def test_responses_client_closes_incomplete_stream_when_cancelled() -> None:
    async def scenario():
        stream = BlockingStream()
        client = ResponsesModelClient("model", client=FakeSDKClient(stream))
        context = ContextBuilder().build(AgentState("task"))
        task = asyncio.create_task(client.generate(context, []))
        await stream.iteration_started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return stream

    stream = asyncio.run(scenario())

    assert stream.closed is True


def test_host_runner_kills_and_waits_for_process_when_cancelled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.communicate_started = asyncio.Event()
            self.killed = False
            self.waited = False

        async def communicate(self):
            self.communicate_started.set()
            await asyncio.Event().wait()

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            return -1

    async def scenario():
        process = FakeProcess()

        async def fake_create_subprocess_shell(*args, **kwargs):
            return process

        monkeypatch.setattr(
            asyncio,
            "create_subprocess_shell",
            fake_create_subprocess_shell,
        )
        task = asyncio.create_task(
            HostRunner().run(CommandRequest("wait", tmp_path, 60))
        )
        await process.communicate_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return process

    process = asyncio.run(scenario())

    assert process.killed is True
    assert process.waited is True
