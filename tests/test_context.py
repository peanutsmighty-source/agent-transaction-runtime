from runtime.context import ContextBuilder
from runtime.observer import ContextObserver
from runtime.state import AgentState, Message


def test_context_observer_groups_items_and_reports_largest_item() -> None:
    state = AgentState(task="Fix a failing test")
    state.messages.extend([
        Message(role="assistant", content="tool_call: {'operation': 'read'}", name="file"),
        Message(role="tool", content="x" * 80, name="file"),
    ])

    context = ContextBuilder("Follow instructions carefully.").build(state)
    stats = ContextObserver(context_window_tokens=100).inspect(context)

    assert stats.item_count == 4
    assert stats.tokens_by_type["system"] > 0
    assert stats.tokens_by_type["task"] > 0
    assert stats.tokens_by_type["assistant"] > 0
    assert stats.tokens_by_type["tool_result"] == 20
    assert stats.largest_items[0].id == "message_1"
    assert state.context_stats.estimated_tokens == 0
