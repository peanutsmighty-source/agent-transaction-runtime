from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI

from ..actions import AssistantMessage, FinalAnswer, ReasoningSummary, ToolCall
from ..context import ContextItem, ContextItemType, ModelContext
from .errors import ModelProtocolError, ModelProviderError, ModelStreamIncompleteError
from .response import ModelResponse, ModelUsage


class ResponsesModelClient:
    """Map the runtime model protocol to a Responses-compatible API."""

    def __init__(
        self,
        model: str,
        *,
        provider_name: str = "responses",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not model:
            raise ValueError("responses_model_must_not_be_empty")
        if not provider_name.strip():
            raise ValueError("responses_provider_name_must_not_be_empty")
        self.model = model
        self.provider_name = provider_name
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )
        self._owns_client = client is None

    async def generate(self, context: ModelContext, tools: list[dict]) -> ModelResponse:
        stream = None
        stream_completed = False
        try:
            stream = await self._client.responses.create(
                model=self.model,
                input=[self._context_item(item) for item in context.items],
                tools=tools,
                stream=True,
            )
            completed: dict[str, Any] | None = None
            output_items: dict[int, dict[str, Any]] = {}
            async for event in stream:
                data = event.model_dump(mode="json")
                event_type = data.get("type")
                if event_type == "response.output_item.done":
                    output_items[int(data["output_index"])] = data["item"]
                elif event_type == "response.completed":
                    completed = data["response"]
                    stream_completed = True
                elif event_type in {"response.failed", "response.incomplete", "error"}:
                    raise ModelProviderError(
                        f"responses_stream_terminal_error: {event_type}",
                        provider=self.provider_name,
                        code=event_type,
                        retryable=False,
                    )
        except ModelProviderError as error:
            if error.provider is None:
                error.provider = self.provider_name
            raise
        except openai.APITimeoutError as error:
            raise ModelProviderError(
                "responses_request_timed_out",
                provider=self.provider_name,
                code="timeout",
                request_id=getattr(error, "request_id", None),
                retryable=True,
            ) from error
        except openai.APIConnectionError as error:
            raise ModelProviderError(
                "responses_connection_failed",
                provider=self.provider_name,
                code="connection_error",
                retryable=True,
            ) from error
        except openai.APIStatusError as error:
            status_code = error.status_code
            raise ModelProviderError(
                f"responses_http_error: {status_code}",
                provider=self.provider_name,
                code="http_error",
                status_code=status_code,
                request_id=error.request_id,
                retryable=status_code in {408, 409, 429} or status_code >= 500,
                detail=getattr(error, "body", None),
            ) from error
        except openai.APIError as error:
            raise ModelProviderError(
                f"responses_sdk_error: {type(error).__name__}",
                provider=self.provider_name,
                code="sdk_error",
            ) from error
        except httpx.HTTPError as error:
            raise ModelProviderError(
                "responses_stream_connection_failed",
                provider=self.provider_name,
                code="stream_connection_error",
                retryable=True,
            ) from error
        finally:
            if stream is not None and not stream_completed:
                close = getattr(stream, "close", None)
                if close is not None:
                    await asyncio.shield(close())

        if completed is None:
            raise ModelStreamIncompleteError(
                "responses_stream_ended_before_response_completed",
                provider=self.provider_name,
                code="stream_incomplete",
                retryable=True,
            )
        if not output_items:
            output_items = {
                index: item for index, item in enumerate(completed.get("output", []))
            }

        try:
            items = self._response_items(
                [output_items[index] for index in sorted(output_items)]
            )
        except ModelProviderError as error:
            if error.provider is None:
                error.provider = self.provider_name
            raise
        return ModelResponse(
            response_id=completed.get("id"),
            items=items,
            usage=self._usage(completed.get("usage")),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    @staticmethod
    def _context_item(item: ContextItem) -> dict[str, Any]:
        if item.type == ContextItemType.SYSTEM:
            return {"role": "system", "content": item.content}
        if item.type == ContextItemType.TASK:
            return {"role": "user", "content": item.content}
        if item.type == ContextItemType.VERIFICATION:
            return {"role": "user", "content": item.content}
        if item.type in (ContextItemType.SUMMARY, ContextItemType.ASSISTANT):
            return {"role": "assistant", "content": item.content}
        if item.type == ContextItemType.TOOL_CALL:
            if not item.tool_call_id or not item.name:
                raise ModelProtocolError("tool_call_context_requires_call_id_and_name")
            try:
                arguments = json.loads(item.content)
            except json.JSONDecodeError as error:
                raise ModelProtocolError("tool_call_context_arguments_are_not_json") from error
            if not isinstance(arguments, dict):
                raise ModelProtocolError("tool_call_context_arguments_must_be_object")
            return {
                "type": "function_call",
                "call_id": item.tool_call_id,
                "name": item.name,
                "arguments": item.content,
            }
        if item.type == ContextItemType.TOOL_RESULT:
            if not item.tool_call_id:
                raise ModelProtocolError("tool_result_context_requires_call_id")
            return {
                "type": "function_call_output",
                "call_id": item.tool_call_id,
                "output": item.content,
            }
        raise ModelProtocolError(f"unsupported_context_item: {item.type}")

    @classmethod
    def _response_items(cls, raw_items: list[dict[str, Any]]) -> list:
        parsed: list = []
        message_positions: list[int] = []
        has_tool_call = any(item.get("type") == "function_call" for item in raw_items)
        for raw in raw_items:
            item_type = raw.get("type")
            if item_type == "reasoning":
                text = "\n".join(
                    part.get("text", "") for part in raw.get("summary", [])
                    if part.get("text")
                )
                if text:
                    parsed.append(ReasoningSummary(text))
            elif item_type == "function_call":
                parsed.append(cls._tool_call(raw))
            elif item_type == "message":
                text = "".join(
                    part.get("text", "")
                    if part.get("type") == "output_text"
                    else part.get("refusal", "")
                    for part in raw.get("content", [])
                    if part.get("type") in {"output_text", "refusal"}
                )
                if text:
                    message_positions.append(len(parsed))
                    parsed.append(AssistantMessage(text))

        if not has_tool_call and message_positions:
            last_message = message_positions[-1]
            parsed[last_message] = FinalAnswer(parsed[last_message].content)
        if not parsed:
            raise ModelProtocolError("responses_has_no_supported_output_items")
        return parsed

    @staticmethod
    def _tool_call(raw: dict[str, Any]) -> ToolCall:
        try:
            arguments = json.loads(raw["arguments"])
            call_id = raw["call_id"]
            name = raw["name"]
        except (KeyError, json.JSONDecodeError) as error:
            raise ModelProtocolError("responses_function_call_is_malformed") from error
        if not isinstance(arguments, dict):
            raise ModelProtocolError("responses_function_call_arguments_must_be_object")
        return ToolCall(call_id=call_id, name=name, arguments=arguments)

    @staticmethod
    def _usage(raw: dict[str, Any] | None) -> ModelUsage:
        if not raw:
            return ModelUsage()
        input_details = raw.get("input_tokens_details") or {}
        output_details = raw.get("output_tokens_details") or {}
        return ModelUsage(
            input_tokens=raw.get("input_tokens", 0),
            cached_input_tokens=input_details.get("cached_tokens", 0),
            output_tokens=raw.get("output_tokens", 0),
            reasoning_tokens=output_details.get("reasoning_tokens", 0),
        )


# Backward-compatible import for callers written before the adapter was generalized.
OpenAIResponsesClient = ResponsesModelClient
