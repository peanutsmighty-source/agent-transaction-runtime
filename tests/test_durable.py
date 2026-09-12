from __future__ import annotations

from datetime import UTC, datetime

import pytest

from runtime.durable import (
    DomainEvent,
    DomainEventType,
    JsonlDomainEventStore,
    TaskNodeStatus,
    reduce_domain_event,
    replay_domain_events,
)


def event(sequence: int, type: DomainEventType, data: dict) -> DomainEvent:
    return DomainEvent(
        event_id=f"evt-{sequence}",
        run_id="run-1",
        sequence=sequence,
        type=type,
        data=data,
        timestamp=datetime(2026, 9, 12, sequence, tzinfo=UTC),
    )


def task_history() -> list[DomainEvent]:
    return [
        event(
            1,
            DomainEventType.TASK_CREATED,
            {
                "task_id": "fix-login",
                "goal": "Fix login timeout",
                "acceptance_criteria": ["login tests pass", "public API unchanged"],
            },
        ),
        event(
            2,
            DomainEventType.PLAN_ACCEPTED,
            {
                "nodes": [
                    {"id": "implement", "description": "Implement the fix"},
                    {
                        "id": "verify",
                        "description": "Run login tests",
                        "dependencies": ["implement"],
                    },
                ]
            },
        ),
        event(3, DomainEventType.TASK_NODE_STARTED, {"node_id": "implement"}),
        event(
            4,
            DomainEventType.TASK_NODE_COMPLETED,
            {"node_id": "implement", "evidence_receipt_ids": ["receipt-file-1"]},
        ),
        event(5, DomainEventType.NEXT_ACTION_SET, {"next_action": "Run login tests"}),
        event(6, DomainEventType.TASK_NODE_STARTED, {"node_id": "verify"}),
    ]


def test_replay_materializes_plan_progress_and_next_work() -> None:
    state = replay_domain_events(task_history())

    assert state.goal == "Fix login timeout"
    assert state.acceptance_criteria == ("login tests pass", "public API unchanged")
    assert state.plan[0].status == TaskNodeStatus.COMPLETED
    assert state.plan[0].evidence_receipt_ids == ("receipt-file-1",)
    assert state.plan[1].status == TaskNodeStatus.RUNNING
    assert state.current_node_id == "verify"
    assert state.next_action == "Run login tests"
    assert state.last_event_sequence == 6


def test_reducer_is_immutable_and_rejects_skipping_dependencies() -> None:
    created = reduce_domain_event(None, task_history()[0])
    planned = reduce_domain_event(created, task_history()[1])

    with pytest.raises(ValueError, match="dependencies_not_completed"):
        reduce_domain_event(
            planned,
            event(3, DomainEventType.TASK_NODE_STARTED, {"node_id": "verify"}),
        )

    assert created.plan == ()
    assert planned.plan[0].status == TaskNodeStatus.PENDING


def test_plan_must_be_acyclic() -> None:
    created = reduce_domain_event(None, task_history()[0])

    with pytest.raises(ValueError, match="must_be_acyclic"):
        reduce_domain_event(
            created,
            event(
                2,
                DomainEventType.PLAN_ACCEPTED,
                {
                    "nodes": [
                        {"id": "a", "description": "A", "dependencies": ["b"]},
                        {"id": "b", "description": "B", "dependencies": ["a"]},
                    ]
                },
            ),
        )


def test_jsonl_store_round_trips_and_reads_after_sequence(tmp_path) -> None:
    store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    history = task_history()

    for item in history:
        assert store.append(item) is True

    assert store.read_all() == history
    assert store.read_after(4) == history[4:]
    assert replay_domain_events(store.read_all()) == replay_domain_events(history)


def test_jsonl_store_treats_exact_duplicate_as_idempotent(tmp_path) -> None:
    store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    first = task_history()[0]

    assert store.append(first) is True
    assert store.append(first) is False
    assert store.read_all() == [first]


def test_jsonl_store_rejects_out_of_order_and_conflicting_events(tmp_path) -> None:
    store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    first = task_history()[0]
    store.append(first)

    with pytest.raises(ValueError, match="out_of_order"):
        store.append(task_history()[2])

    conflicting = DomainEvent(
        event_id="different-id",
        run_id="run-1",
        sequence=1,
        type=DomainEventType.TASK_CREATED,
        data={"task_id": "other", "goal": "Other", "acceptance_criteria": []},
        timestamp=first.timestamp,
    )
    with pytest.raises(ValueError, match="conflicting_domain_event"):
        store.append(conflicting)


def test_replay_rejects_sequence_gaps() -> None:
    history = task_history()

    with pytest.raises(ValueError, match="sequence_mismatch"):
        replay_domain_events([history[0], history[2]])


def test_blockers_are_materialized_and_resolved() -> None:
    history = task_history()
    state = replay_domain_events(
        history
        + [
            event(
                7,
                DomainEventType.BLOCKER_ADDED,
                {"blocker_id": "api-down", "description": "Provider is unavailable"},
            ),
            event(
                8,
                DomainEventType.BLOCKER_RESOLVED,
                {"blocker_id": "api-down"},
            ),
        ]
    )

    assert state.blockers == ()
    assert state.last_event_sequence == 8


def test_store_rejects_corrupt_jsonl(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_domain_event_at_line_1"):
        JsonlDomainEventStore(path).read_all()
