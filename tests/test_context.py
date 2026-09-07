from runtime.context import (
    ContextBuilder,
    ContextItem,
    ContextItemType,
    ModelContext,
    group_context_units,
)
from runtime.observer import ContextObserver
from runtime.state import AgentState, Message


def test_context_observer_groups_items_and_reports_largest_item() -> None:
    state = AgentState(task="Fix a failing test")
    state.messages.extend([
        Message(role="assistant", content='{"operation":"read"}', name="file", tool_call_id="call_1"),
        Message(role="tool", content="x" * 80, name="file", tool_call_id="call_1"),
    ])

    context = ContextBuilder("Follow instructions carefully.").build(state)
    stats = ContextObserver(context_window_tokens=100).inspect(context)

    assert stats.item_count == 4
    assert stats.tokens_by_type["system"] > 0
    assert stats.tokens_by_type["task"] > 0
    assert stats.tokens_by_type["tool_call"] > 0
    assert stats.tokens_by_type["tool_result"] == 20
    assert stats.largest_items[0].id == "message_1"
    assert state.context_stats.estimated_tokens == 0


def test_model_context_maps_summary_and_tool_result_to_model_roles() -> None:
    context = ModelContext(items=[
        ContextItem("summary", ContextItemType.SUMMARY, "Earlier facts", 3, name="full_summary"),
        ContextItem(
            "result",
            ContextItemType.TOOL_RESULT,
            "ok",
            1,
            name="file",
            tool_call_id="call_1",
        ),
    ])

    assert [message.role for message in context.messages] == ["assistant", "tool"]
    assert context.messages[0].name == "full_summary"
    assert context.messages[1].tool_call_id == "call_1"


def test_multiple_tool_calls_and_results_form_one_atomic_batch_unit() -> None:
    items = [
        ContextItem("call_1", ContextItemType.TOOL_CALL, "{}", 1, "file", "call_1"),
        ContextItem("call_2", ContextItemType.TOOL_CALL, "{}", 1, "file", "call_2"),
        ContextItem("result_1", ContextItemType.TOOL_RESULT, "one", 1, "file", "call_1"),
        ContextItem("result_2", ContextItemType.TOOL_RESULT, "two", 1, "file", "call_2"),
    ]

    units = group_context_units(items)

    assert len(units) == 1
    assert units[0].item_ids == ["call_1", "call_2", "result_1", "result_2"]
