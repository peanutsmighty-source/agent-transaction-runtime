from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import httpx

from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder, ContextItem, ContextItemType
from runtime.loop import AgentLoop, RunConfig
from runtime.policies import ModelRetryPolicy
from runtime.events import EventType
from runtime.model import (
    ModelProviderError,
    ModelStreamIncompleteError,
    OpenAIResponsesClient,
    ResponsesModelClient,
)
from runtime.state import AgentState, AgentStatus, Message
from runtime.summarization import ModelSummarizer, SummaryRequest
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


@dataclass
class StreamScript:
    chunks: list[bytes]
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    delay_before_headers: float = 0


class MockResponsesServer:
    def __init__(self, scripts: list[StreamScript]) -> None:
        self.scripts = scripts
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                owner.requests.append(json.loads(self.rfile.read(length)))
                script = owner.scripts.pop(0)
                if script.delay_before_headers:
                    time.sleep(script.delay_before_headers)
                self.send_response(script.status)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if script.status == 200 else "application/json",
                )
                self.send_header("Connection", "close")
                for name, value in script.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    for chunk in script.chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> MockResponsesServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


def sse(event: dict[str, Any]) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def message_item(text: str) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
        ],
    }


def function_item(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "fc_1",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_1",
        "name": "file",
        "arguments": json.dumps(arguments),
    }


def done_item(item: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return {
        "type": "response.output_item.done",
        "sequence_number": index + 1,
        "output_index": index,
        "item": item,
    }


def completed(output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "sequence_number": 99,
        "response": {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "test-model",
            "output": output,
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "metadata": {},
            "parallel_tool_calls": True,
            "temperature": 1,
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1,
            "background": False,
            "max_output_tokens": None,
            "max_tool_calls": None,
            "previous_response_id": None,
            "prompt": None,
            "prompt_cache_key": None,
            "reasoning": None,
            "safety_identifier": None,
            "service_tier": "default",
            "store": True,
            "text": {"format": {"type": "text"}},
            "truncation": "disabled",
            "usage": {
                "input_tokens": 11,
                "input_tokens_details": {"cached_tokens": 3},
                "output_tokens": 7,
                "output_tokens_details": {"reasoning_tokens": 2},
                "total_tokens": 18,
            },
        },
    }


def run_generate(
    server: MockResponsesServer,
    state: AgentState | None = None,
    *,
    max_retries: int = 0,
    timeout: float = 60,
):
    async def run():
        client = ResponsesModelClient(
            "test-model",
            provider_name="mock-responses",
            api_key="test-key",
            base_url=server.base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx.AsyncClient(trust_env=False),
        )
        try:
            context = ContextBuilder("system").build(state or AgentState(task="task"))
            return await client.generate(context, [FileTool.__new__(FileTool).schema()])
        finally:
            await client.close()

    return asyncio.run(run())


def test_stream_reassembles_sse_split_at_arbitrary_byte_boundaries() -> None:
    item = message_item("split stream works")
    payload = sse(done_item(item)) + sse(completed([item]))
    chunks = [payload[:7], payload[7:31], payload[31:100], payload[100:]]

    with MockResponsesServer([StreamScript(chunks)]) as server:
        response = run_generate(server)

    assert response.items == [FinalAnswer("split stream works")]
    assert response.response_id == "resp_1"
    assert response.usage.cached_input_tokens == 3
    assert server.requests[0]["stream"] is True
    assert server.requests[0]["input"][:2] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]


def test_tool_call_and_result_remain_structured_in_next_request() -> None:
    call = function_item({"operation": "list", "path": "."})
    with MockResponsesServer(
        [
            StreamScript([sse(done_item(call)), sse(completed([call]))]),
            StreamScript(
                [
                    sse(done_item(message_item("finished"))),
                    sse(completed([message_item("finished")])),
                ]
            ),
        ]
    ) as server:
        first = run_generate(server)
        assert first.items == [
            ToolCall("call_1", "file", {"operation": "list", "path": "."})
        ]
        state = AgentState(task="task")
        state.messages.extend(
            [
                Message(
                    role="assistant",
                    name="file",
                    tool_call_id="call_1",
                    content='{"operation":"list","path":"."}',
                ),
                Message(
                    role="tool",
                    name="file",
                    tool_call_id="call_1",
                    content='{"success":true}',
                ),
            ]
        )
        second = run_generate(server, state)

    assert second.items == [FinalAnswer("finished")]
    assert server.requests[1]["input"][2:] == [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "file",
            "arguments": '{"operation":"list","path":"."}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"success":true}',
        },
    ]


def test_multiple_tool_calls_are_sent_before_their_outputs_on_next_request(
    tmp_path,
) -> None:
    first_call = function_item({"operation": "list", "path": "."})
    second_call = {
        **function_item({"operation": "read", "path": "marker.txt"}),
        "id": "fc_2",
        "call_id": "call_2",
    }
    answer = message_item("finished")
    (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
    with MockResponsesServer(
        [
            StreamScript(
                [
                    sse(done_item(first_call, 0)),
                    sse(done_item(second_call, 1)),
                    sse(completed([first_call, second_call])),
                ]
            ),
            StreamScript([sse(done_item(answer)), sse(completed([answer]))]),
        ]
    ) as server:
        async def run():
            client = ResponsesModelClient(
                "test-model",
                provider_name="mock-responses",
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            registry = ToolRegistry()
            registry.register(FileTool(tmp_path))
            loop = AgentLoop(
                client,
                ToolRuntime(registry),
                ContextBuilder("system"),
                RunConfig(max_steps=3, trace_root=tmp_path / ".runs"),
            )
            try:
                return await loop.run("inspect")
            finally:
                await client.close()

        state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert [item["type"] for item in server.requests[1]["input"][2:]] == [
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
    ]
    assert [item["call_id"] for item in server.requests[1]["input"][2:]] == [
        "call_1",
        "call_2",
        "call_1",
        "call_2",
    ]


def test_stream_disconnect_before_completed_is_not_success() -> None:
    delta = {
        "type": "response.output_text.delta",
        "sequence_number": 1,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": "partial",
        "logprobs": [],
    }
    with MockResponsesServer([StreamScript([sse(delta)])]) as server:
        with pytest.raises(ModelStreamIncompleteError) as error:
            run_generate(server)

    assert str(error.value) == "responses_stream_ended_before_response_completed"
    assert error.value.provider == "mock-responses"


def test_real_agent_loop_uses_mock_responses_and_executes_tool(tmp_path) -> None:
    call = function_item({"operation": "list", "path": "."})
    answer = message_item("workspace inspected")
    with MockResponsesServer(
        [
            StreamScript([sse(done_item(call)), sse(completed([call]))]),
            StreamScript([sse(done_item(answer)), sse(completed([answer]))]),
        ]
    ) as server:
        async def run():
            client = ResponsesModelClient(
                "test-model",
                provider_name="mock-responses",
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            registry = ToolRegistry()
            registry.register(FileTool(tmp_path))
            loop = AgentLoop(
                client,
                ToolRuntime(registry),
                ContextBuilder("system"),
                RunConfig(max_steps=3, trace_root=tmp_path / ".runs"),
            )
            try:
                return await loop.run("inspect workspace")
            finally:
                await client.close()

        state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert state.final_answer == "workspace inspected"
    assert len(state.tool_history) == 1
    assert state.tool_history[0].result["success"] is True
    assert server.requests[1]["input"][2]["type"] == "function_call"
    assert server.requests[1]["input"][3]["type"] == "function_call_output"


@pytest.mark.parametrize("status", [429, 500])
def test_sdk_retries_retryable_http_status_then_succeeds(status: int) -> None:
    answer = message_item("recovered")
    error_body = json.dumps(
        {
            "error": {
                "message": "temporary failure",
                "type": "server_error",
                "param": None,
                "code": "temporary_failure",
            }
        }
    ).encode()
    with MockResponsesServer(
        [
            StreamScript(
                [error_body],
                status=status,
                headers={"Retry-After": "0", "x-request-id": "req_retry"},
            ),
            StreamScript([sse(done_item(answer)), sse(completed([answer]))]),
        ]
    ) as server:
        response = run_generate(server, max_retries=1)

    assert response.items == [FinalAnswer("recovered")]
    assert len(server.requests) == 2


def test_runtime_retry_policy_recovers_from_429_with_sdk_retry_disabled(
    tmp_path,
) -> None:
    error_body = json.dumps(
        {
            "error": {
                "message": "rate limited",
                "type": "rate_limit_error",
                "param": None,
                "code": "rate_limit_exceeded",
            }
        }
    ).encode()
    answer = message_item("runtime retry recovered")
    with MockResponsesServer(
        [
            StreamScript([error_body], status=429),
            StreamScript([sse(done_item(answer)), sse(completed([answer]))]),
        ]
    ) as server:
        async def run():
            client = ResponsesModelClient(
                "test-model",
                provider_name="mock-responses",
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            loop = AgentLoop(
                client,
                ToolRuntime(ToolRegistry()),
                ContextBuilder("system"),
                RunConfig(trace_root=tmp_path / ".runs"),
                model_retry_policy=ModelRetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0,
                ),
            )
            try:
                return await loop.run("recover from rate limit")
            finally:
                await client.close()

        state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert state.final_answer == "runtime retry recovered"
    assert len(server.requests) == 2
    assert EventType.MODEL_RETRY_SCHEDULED in [event.type for event in state.events]


def test_runtime_retry_policy_recovers_from_incomplete_sse_stream(
    tmp_path,
) -> None:
    partial_delta = {
        "type": "response.output_text.delta",
        "sequence_number": 1,
        "item_id": "msg_partial",
        "output_index": 0,
        "content_index": 0,
        "delta": "partial text that must not be committed",
        "logprobs": [],
    }
    answer = message_item("fresh complete response")
    with MockResponsesServer(
        [
            StreamScript([sse(partial_delta)]),
            StreamScript([sse(done_item(answer)), sse(completed([answer]))]),
        ]
    ) as server:
        async def run():
            client = ResponsesModelClient(
                "test-model",
                provider_name="mock-responses",
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            loop = AgentLoop(
                client,
                ToolRuntime(ToolRegistry()),
                ContextBuilder("system"),
                RunConfig(trace_root=tmp_path / ".runs"),
                model_retry_policy=ModelRetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0,
                ),
            )
            try:
                return await loop.run("recover from stream disconnect")
            finally:
                await client.close()

        state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert state.final_answer == "fresh complete response"
    assert state.messages == []
    assert len(server.requests) == 2


def test_model_summarizer_uses_real_responses_adapter_without_tools() -> None:
    answer = message_item("Keep the public API stable.")
    with MockResponsesServer(
        [StreamScript([sse(done_item(answer)), sse(completed([answer]))])]
    ) as server:
        async def run() -> str:
            client = ResponsesModelClient(
                "summary-model",
                provider_name="mock-summarizer",
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            summarizer = ModelSummarizer(client)
            try:
                return await summarizer.summarize(
                    SummaryRequest(
                        items=(
                            ContextItem(
                                id="old_decision",
                                type=ContextItemType.ASSISTANT,
                                content="The public API must remain stable.",
                                token_count=9,
                            ),
                        ),
                        max_tokens=40,
                    )
                )
            finally:
                await client.close()

        summary = asyncio.run(run())

    assert summary == "Keep the public API stable."
    assert server.requests[0]["model"] == "summary-model"
    assert server.requests[0]["tools"] == []
    assert [item["role"] for item in server.requests[0]["input"]] == [
        "system",
        "user",
    ]
    assert "old_decision" in server.requests[0]["input"][1]["content"]


def test_timeout_is_normalized_as_retryable_provider_error() -> None:
    with MockResponsesServer(
        [StreamScript([], delay_before_headers=0.2)]
    ) as server:
        with pytest.raises(ModelProviderError) as error:
            run_generate(server, timeout=0.05)

    assert str(error.value) == "responses_request_timed_out"
    assert error.value.code == "timeout"
    assert error.value.retryable is True


def test_http_error_diagnostics_are_written_to_runtime_event(tmp_path) -> None:
    error_body = json.dumps(
        {
            "error": {
                "message": "rate limited",
                "type": "rate_limit_error",
                "param": None,
                "code": "rate_limit_exceeded",
            }
        }
    ).encode()
    with MockResponsesServer(
        [
            StreamScript(
                [error_body],
                status=429,
                headers={"x-request-id": "req_rate_limit"},
            )
        ]
    ) as server:
        async def run():
            client = ResponsesModelClient(
                "test-model",
                provider_name="deepseek-test",
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            loop = AgentLoop(
                client,
                ToolRuntime(ToolRegistry()),
                ContextBuilder("system"),
                RunConfig(max_steps=1, trace_root=tmp_path / ".runs"),
            )
            try:
                return await loop.run("task")
            finally:
                await client.close()

        state = asyncio.run(run())

    event = next(event for event in state.events if event.type == EventType.RUNTIME_ERROR)
    assert state.status == AgentStatus.FAILED
    assert event.data["status_code"] == 429
    assert event.data["provider"] == "deepseek-test"
    assert event.data["request_id"] == "req_rate_limit"
    assert event.data["retryable"] is True
    assert event.data["detail"]["code"] == "rate_limit_exceeded"


def test_stream_error_event_is_not_treated_as_completion() -> None:
    error_event = {
        "type": "error",
        "sequence_number": 1,
        "code": "server_error",
        "message": "generation failed",
        "param": None,
    }
    with MockResponsesServer([StreamScript([sse(error_event)])]) as server:
        with pytest.raises(ModelProviderError) as error:
            run_generate(server)

    assert error.value.code == "error"
    assert error.value.retryable is False


def test_old_openai_client_name_remains_a_compatibility_alias() -> None:
    assert OpenAIResponsesClient is ResponsesModelClient
