from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder
from runtime.events import EventType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import ModelProviderError, ModelResponse, ModelStreamIncompleteError
from runtime.policies import ModelRetryPolicy
from runtime.state import AgentState, AgentStatus
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


class ScriptedModel:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.requests = []

    async def generate(self, context, tools):
        self.requests.append(context)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_loop(tmp_path: Path, model, policy: ModelRetryPolicy) -> AgentLoop:
    return AgentLoop(
        model,
        ToolRuntime(ToolRegistry()),
        ContextBuilder(),
        RunConfig(trace_root=tmp_path / ".runs"),
        model_retry_policy=policy,
    )


def retryable_error(code: str = "connection_error") -> ModelProviderError:
    return ModelProviderError(
        "temporary failure",
        provider="test",
        code=code,
        retryable=True,
    )


def test_retryable_model_failure_reuses_context_then_succeeds(tmp_path: Path) -> None:
    model = ScriptedModel(
        [retryable_error(), ModelResponse(items=[FinalAnswer("recovered")])]
    )
    loop = make_loop(
        tmp_path,
        model,
        ModelRetryPolicy(max_attempts=2, base_delay_seconds=0),
    )

    state = asyncio.run(loop.run("recover"))

    assert state.status == AgentStatus.COMPLETED
    assert state.final_answer == "recovered"
    assert len(model.requests) == 2
    assert model.requests[0].to_dict() == model.requests[1].to_dict()
    scheduled = [
        event for event in state.events
        if event.type == EventType.MODEL_RETRY_SCHEDULED
    ]
    assert scheduled[0].data["failed_attempt"] == 1
    assert scheduled[0].data["next_attempt"] == 2
    assert state.summary()["model_retries"] == 1
    assert state.summary()["model_calls"] == 2


def test_stream_incomplete_is_retryable_before_any_items_are_committed(
    tmp_path: Path,
) -> None:
    model = ScriptedModel(
        [
            ModelStreamIncompleteError(
                "stream ended",
                provider="test",
                code="stream_incomplete",
                retryable=True,
            ),
            ModelResponse(items=[FinalAnswer("complete response")]),
        ]
    )

    state = asyncio.run(
        make_loop(
            tmp_path,
            model,
            ModelRetryPolicy(max_attempts=2, base_delay_seconds=0),
        ).run("retry stream")
    )

    assert state.status == AgentStatus.COMPLETED
    assert state.messages == []
    assert len(model.requests) == 2


def test_non_retryable_error_fails_without_another_request(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            ModelProviderError(
                "bad request",
                provider="test",
                code="http_error",
                status_code=400,
                retryable=False,
            )
        ]
    )

    state = asyncio.run(
        make_loop(
            tmp_path,
            model,
            ModelRetryPolicy(max_attempts=3, base_delay_seconds=0),
        ).run("do not retry")
    )

    assert state.status == AgentStatus.FAILED
    assert len(model.requests) == 1
    exhausted = next(
        event for event in state.events
        if event.type == EventType.MODEL_RETRY_EXHAUSTED
    )
    assert exhausted.data["reason"] == "provider_marked_non_retryable"


def test_retry_stops_at_attempt_limit(tmp_path: Path) -> None:
    model = ScriptedModel([retryable_error(), retryable_error()])

    state = asyncio.run(
        make_loop(
            tmp_path,
            model,
            ModelRetryPolicy(max_attempts=2, base_delay_seconds=0),
        ).run("bounded retries")
    )

    assert state.status == AgentStatus.FAILED
    assert len(model.requests) == 2
    exhausted = [
        event for event in state.events
        if event.type == EventType.MODEL_RETRY_EXHAUSTED
    ]
    assert exhausted[-1].data["reason"] == "max_attempts_exhausted"


def test_retry_stops_before_estimated_input_token_budget_is_exceeded(
    tmp_path: Path,
) -> None:
    model = ScriptedModel([retryable_error()])
    context_tokens = ContextBuilder().build(AgentState("token budget")).estimated_tokens

    state = asyncio.run(
        make_loop(
            tmp_path,
            model,
            ModelRetryPolicy(
                max_attempts=3,
                base_delay_seconds=0,
                max_estimated_input_tokens=context_tokens,
            ),
        ).run("token budget")
    )

    assert state.status == AgentStatus.FAILED
    assert len(model.requests) == 1
    exhausted = next(
        event for event in state.events
        if event.type == EventType.MODEL_RETRY_EXHAUSTED
    )
    assert exhausted.data["reason"] == "estimated_input_token_budget_exhausted"


def test_retrying_later_model_request_does_not_reexecute_previous_tool(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(FileTool(tmp_path))
    model = ScriptedModel(
        [
            ModelResponse(
                items=[ToolCall("call_list", "file", {"operation": "list", "path": "."})]
            ),
            retryable_error("timeout"),
            ModelResponse(items=[FinalAnswer("finished")]),
        ]
    )
    loop = AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(trace_root=tmp_path / ".runs"),
        model_retry_policy=ModelRetryPolicy(max_attempts=2, base_delay_seconds=0),
    )

    state = asyncio.run(loop.run("list once"))

    assert state.status == AgentStatus.COMPLETED
    assert len(state.tool_history) == 1
    assert state.tool_history[0].call_id == "call_list"
    assert len(model.requests) == 3


def test_retry_delay_uses_capped_exponential_backoff() -> None:
    policy = ModelRetryPolicy(
        max_attempts=4,
        base_delay_seconds=0.5,
        max_delay_seconds=0.75,
    )
    error = retryable_error()

    first = policy.evaluate(error, failed_attempt=1, estimated_input_tokens=10)
    second = policy.evaluate(error, failed_attempt=2, estimated_input_tokens=10)

    assert first.delay_seconds == 0.5
    assert second.delay_seconds == 0.75
