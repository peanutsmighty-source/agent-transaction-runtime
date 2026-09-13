from __future__ import annotations

from datetime import UTC, datetime

import pytest

from runtime.durable import (
    DomainEvent,
    DomainEventType,
    DurableTaskSession,
    JsonlDomainEventStore,
    JsonTaskCheckpointStore,
    TaskCheckpoint,
    TaskNodeStatus,
    reduce_domain_event,
    replay_domain_events,
    replay_from_checkpoint,
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


def test_checkpoint_contains_plan_and_delta_replay_matches_full_replay(tmp_path) -> None:
    history = task_history()
    state_at_checkpoint = replay_domain_events(history[:4])
    checkpoint = TaskCheckpoint.create(
        task_state=state_at_checkpoint,
        checkpoint_sequence=1,
        workspace_revision="git-tree-abc",
        checkpoint_id="cp-1",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    store = JsonTaskCheckpointStore(tmp_path / "checkpoints", "run-1")

    assert store.save(checkpoint) is True
    assert store.save(checkpoint) is False
    loaded = store.load_latest()
    resumed = replay_from_checkpoint(loaded, history[4:])

    assert loaded.task_state.plan[0].status == TaskNodeStatus.COMPLETED
    assert loaded.task_state.plan[1].status == TaskNodeStatus.PENDING
    assert resumed == replay_domain_events(history)


def test_checkpoint_store_loads_latest_committed_sequence(tmp_path) -> None:
    history = task_history()
    store = JsonTaskCheckpointStore(tmp_path / "checkpoints", "run-1")
    first = TaskCheckpoint.create(
        task_state=replay_domain_events(history[:2]),
        checkpoint_sequence=1,
        checkpoint_id="cp-1",
        created_at=datetime(2026, 9, 13, 1, tzinfo=UTC),
    )
    second = TaskCheckpoint.create(
        task_state=replay_domain_events(history[:4]),
        checkpoint_sequence=2,
        checkpoint_id="cp-2",
        created_at=datetime(2026, 9, 13, 2, tzinfo=UTC),
    )

    store.save(first)
    store.save(second)

    assert store.load_latest() == second


def test_checkpoint_checksum_detects_tampering(tmp_path) -> None:
    checkpoint = TaskCheckpoint.create(
        task_state=replay_domain_events(task_history()[:2]),
        checkpoint_sequence=1,
        checkpoint_id="cp-1",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    store = JsonTaskCheckpointStore(tmp_path / "checkpoints", "run-1")
    store.save(checkpoint)
    path = tmp_path / "checkpoints" / "run-1" / "checkpoint-00000001.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Fix login timeout", "Pretend the goal changed"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checkpoint_checksum_mismatch"):
        store.load_latest()


def test_checkpoint_replay_rejects_an_already_applied_event() -> None:
    history = task_history()
    checkpoint = TaskCheckpoint.create(
        task_state=replay_domain_events(history[:4]),
        checkpoint_sequence=1,
    )

    with pytest.raises(ValueError, match="already_applied_event"):
        replay_from_checkpoint(checkpoint, [history[3]])


def test_checkpoint_store_rejects_unsafe_run_id(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsafe_characters"):
        JsonTaskCheckpointStore(tmp_path, "../other-run")


def test_durable_session_recovers_checkpoint_plus_delta(tmp_path) -> None:
    event_store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    checkpoint_store = JsonTaskCheckpointStore(
        tmp_path / "checkpoints", "run-session"
    )
    session = DurableTaskSession.create(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
        task_id="fix-login",
        goal="Fix login timeout",
        acceptance_criteria=["login tests pass"],
    )
    session.accept_plan(
        [
            {"id": "implement", "description": "Implement the fix"},
            {
                "id": "verify",
                "description": "Run tests",
                "dependencies": ["implement"],
            },
        ]
    )
    session.start_node("implement")
    session.complete_node("implement", evidence_receipt_ids=["receipt-file-1"])
    checkpoint = session.save_checkpoint("git-tree-abc")
    session.set_next_action("Run login tests")
    session.start_node("verify")

    resumed = DurableTaskSession.resume(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
    )

    assert checkpoint.metadata.event_sequence == 4
    assert resumed.state == session.state
    assert resumed.state.current_node_id == "verify"
    assert resumed.state.next_action == "Run login tests"


def test_durable_session_recovers_without_checkpoint(tmp_path) -> None:
    event_store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    checkpoint_store = JsonTaskCheckpointStore(
        tmp_path / "checkpoints", "run-session"
    )
    session = DurableTaskSession.create(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
        task_id="task",
        goal="Goal",
    )
    session.accept_plan([{"id": "work", "description": "Do the work"}])

    resumed = DurableTaskSession.resume(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
    )

    assert resumed.state == session.state


def test_durable_session_recovers_event_persisted_before_memory_update(tmp_path) -> None:
    event_store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    checkpoint_store = JsonTaskCheckpointStore(
        tmp_path / "checkpoints", "run-session"
    )
    session = DurableTaskSession.create(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
        task_id="task",
        goal="Goal",
    )
    session.accept_plan([{"id": "work", "description": "Do the work"}])
    session.save_checkpoint()
    persisted_before_crash = DomainEvent.create(
        run_id="run-session",
        sequence=3,
        type=DomainEventType.TASK_NODE_STARTED,
        data={"node_id": "work"},
    )
    event_store.append(persisted_before_crash)

    resumed = DurableTaskSession.resume(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
    )

    assert resumed.state.current_node_id == "work"
    assert resumed.state.last_event_sequence == 3


def test_durable_session_does_not_persist_invalid_transition(tmp_path) -> None:
    event_store = JsonlDomainEventStore(tmp_path / "events.jsonl")
    checkpoint_store = JsonTaskCheckpointStore(
        tmp_path / "checkpoints", "run-session"
    )
    session = DurableTaskSession.create(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
        task_id="task",
        goal="Goal",
    )
    session.accept_plan(
        [
            {"id": "first", "description": "First"},
            {"id": "second", "description": "Second", "dependencies": ["first"]},
        ]
    )

    with pytest.raises(ValueError, match="dependencies_not_completed"):
        session.start_node("second")

    assert [item.sequence for item in event_store.read_all()] == [1, 2]
    assert session.state.last_event_sequence == 2
