import asyncio

from runtime.context import ContextBuilder, ContextItem, ContextItemType, ModelContext
from runtime.policies import FullSummaryCompaction, SlidingWindowCompaction
from runtime.state import AgentState, Message
from runtime.summarization import FakeSummarizer


def test_sliding_window_keeps_system_task_and_recent_suffix() -> None:
    state = AgentState(task="keep the task")
    state.messages.extend(
        Message(role="assistant", content=character * 40)
        for character in ("a", "b", "c", "d")
    )
    context = ContextBuilder("keep the system").build(state)

    result = asyncio.run(
        SlidingWindowCompaction(min_recent_units=2).compact(
            context,
            target_tokens=30,
        )
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

    result = asyncio.run(
        SlidingWindowCompaction().compact(context, target_tokens=1_000)
    )

    assert result.context.items == context.items
    assert result.removed_item_ids == []
    assert result.applied is False


def test_sliding_window_never_splits_tool_call_and_result() -> None:
    context = ModelContext(items=[
        ContextItem("system", ContextItemType.SYSTEM, "system", 2),
        ContextItem("task", ContextItemType.TASK, "task", 1),
        ContextItem(
            "tool_call",
            ContextItemType.TOOL_CALL,
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

    result = asyncio.run(
        SlidingWindowCompaction(min_recent_units=1).compact(
            context,
            target_tokens=20,
        )
    )

    assert [item.id for item in result.context.items] == [
        "system",
        "task",
        "recent",
    ]
    assert result.removed_item_ids == ["tool_call", "tool_result"]


def test_full_summary_keeps_mandatory_recent_and_summarizes_complete_units() -> None:
    context = ModelContext(items=[
        ContextItem("system", ContextItemType.SYSTEM, "system", 2),
        ContextItem("task", ContextItemType.TASK, "task", 1),
        ContextItem("old", ContextItemType.ASSISTANT, "old decision", 12),
        ContextItem(
            "tool_call",
            ContextItemType.TOOL_CALL,
            "call",
            12,
            name="file",
            tool_call_id="call_1",
        ),
        ContextItem(
            "tool_result",
            ContextItemType.TOOL_RESULT,
            "result",
            12,
            name="file",
            tool_call_id="call_1",
        ),
        ContextItem("recent_1", ContextItemType.ASSISTANT, "recent one", 10),
        ContextItem("recent_2", ContextItemType.ASSISTANT, "recent two", 10),
    ])
    summarizer = FakeSummarizer(["Keep the public API stable."])

    result = asyncio.run(
        FullSummaryCompaction(summarizer, min_recent_units=2).compact(
            context,
            target_tokens=30,
        )
    )

    assert [item.id for item in result.context.items] == [
        "system",
        "task",
        "summary",
        "recent_1",
        "recent_2",
    ]
    assert result.context.items[2].type == ContextItemType.SUMMARY
    assert result.summarized_item_ids == ["old", "tool_call", "tool_result"]
    assert result.retained_item_ids == ["recent_1", "recent_2"]
    assert [item.id for item in summarizer.requests[0].items] == [
        "old",
        "tool_call",
        "tool_result",
    ]
    assert result.compacted_tokens <= result.target_tokens


def test_full_summary_failure_falls_back_without_mutating_source_context() -> None:
    state = AgentState(task="keep the task")
    state.messages.extend(
        Message(role="assistant", content=character * 40)
        for character in ("a", "b", "c", "d")
    )
    context = ContextBuilder("keep the system").build(state)
    original_items = list(context.items)
    summarizer = FakeSummarizer([RuntimeError("summary service unavailable")])

    result = asyncio.run(
        FullSummaryCompaction(summarizer, min_recent_units=2).compact(
            context,
            target_tokens=30,
        )
    )

    assert result.error == "summarizer_failed"
    assert result.error_detail == "RuntimeError: summary service unavailable"
    assert result.fallback_strategy == "sliding_window"
    assert [item.id for item in result.context.items] == [
        "system",
        "task",
        "message_2",
        "message_3",
    ]
    assert context.items == original_items
    assert len(state.messages) == 4


def test_full_summary_rejects_summary_that_exceeds_its_budget() -> None:
    state = AgentState(task="task")
    state.messages.extend(
        Message(role="assistant", content=character * 40)
        for character in ("a", "b", "c")
    )
    context = ContextBuilder("system").build(state)

    result = asyncio.run(
        FullSummaryCompaction(
            FakeSummarizer(["summary that is intentionally much too long" * 4]),
            min_recent_units=1,
        ).compact(context, target_tokens=25)
    )

    assert result.error is not None
    assert result.error == "summary_exceeds_budget"
    assert result.error_detail is not None
    assert "summary_budget=" in result.error_detail
    assert result.fallback_strategy == "sliding_window"
    assert all(item.type != ContextItemType.SUMMARY for item in result.context.items)
