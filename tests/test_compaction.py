from runtime.context import ContextBuilder, ContextItem, ContextItemType, ModelContext
from runtime.policies import SlidingWindowCompaction
from runtime.state import AgentState, Message


def test_sliding_window_keeps_system_task_and_recent_suffix() -> None:
    state = AgentState(task="keep the task")
    state.messages.extend(
        Message(role="assistant", content=character * 40)
        for character in ("a", "b", "c", "d")
    )
    context = ContextBuilder("keep the system").build(state)

    result = SlidingWindowCompaction(min_recent_units=2).compact(
        context,
        target_tokens=30,
    )

    assert [item.id for item in result.context.items] == [
        "system",
        "task",
        "message_2",
        "message_3",
    ]
    assert result.removed_item_ids == ["message_0", "message_1"]
    assert result.compacted_tokens < result.original_tokens
    assert result.applied is True


def test_sliding_window_keeps_full_context_when_it_fits() -> None:
    state = AgentState(task="small")
    state.messages.append(Message(role="assistant", content="done"))
    context = ContextBuilder().build(state)

    result = SlidingWindowCompaction().compact(context, target_tokens=1_000)

    assert result.context.items == context.items
    assert result.removed_item_ids == []
    assert result.applied is False


def test_sliding_window_never_splits_tool_call_and_result() -> None:
    context = ModelContext(items=[
        ContextItem("system", ContextItemType.SYSTEM, "system", 2),
        ContextItem("task", ContextItemType.TASK, "task", 1),
        ContextItem(
            "tool_call",
            ContextItemType.ASSISTANT,
            "call",
            8,
            name="file",
            tool_call_id="call_1",
        ),
        ContextItem(
            "tool_result",
            ContextItemType.TOOL_RESULT,
            "result",
            8,
            name="file",
            tool_call_id="call_1",
        ),
        ContextItem("recent", ContextItemType.ASSISTANT, "recent", 8),
    ])

    result = SlidingWindowCompaction(min_recent_units=1).compact(
        context,
        target_tokens=20,
    )

    assert [item.id for item in result.context.items] == [
        "system",
        "task",
        "recent",
    ]
    assert result.removed_item_ids == ["tool_call", "tool_result"]
