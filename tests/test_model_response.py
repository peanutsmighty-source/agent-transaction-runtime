import pytest

from runtime.actions import FinalAnswer, ToolCall
from runtime.model import ModelResponse


def test_model_response_rejects_empty_items() -> None:
    with pytest.raises(ValueError, match="model_response_items_must_not_be_empty"):
        ModelResponse(items=[])


def test_model_response_rejects_final_answer_mixed_with_tool_call() -> None:
    with pytest.raises(ValueError, match="model_response_cannot_mix_tool_calls_and_final_answer"):
        ModelResponse(items=[ToolCall("call_1", "file", {}), FinalAnswer("Done")])
