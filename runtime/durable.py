from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


DOMAIN_EVENT_SCHEMA_VERSION = 1


class DomainEventType(StrEnum):
    """State-changing facts that must survive process restarts."""

    TASK_CREATED = "task_created"
    PLAN_ACCEPTED = "plan_accepted"
    TASK_NODE_STARTED = "task_node_started"
    TASK_NODE_COMPLETED = "task_node_completed"
    BLOCKER_ADDED = "blocker_added"
    BLOCKER_RESOLVED = "blocker_resolved"
    NEXT_ACTION_SET = "next_action_set"


class TaskNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass(frozen=True)
class TaskNode:
    id: str
    description: str
    dependencies: tuple[str, ...] = ()
    status: TaskNodeStatus = TaskNodeStatus.PENDING
    evidence_receipt_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "evidence_receipt_ids": list(self.evidence_receipt_ids),
        }


@dataclass(frozen=True)
class TaskBlocker:
    id: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "description": self.description}


@dataclass(frozen=True)
class DurableTaskState:
    """Materialized task state produced only by reducing domain events."""

    run_id: str
    task_id: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    plan: tuple[TaskNode, ...] = ()
    current_node_id: str | None = None
    blockers: tuple[TaskBlocker, ...] = ()
    next_action: str | None = None
    last_event_sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "plan": [node.to_dict() for node in self.plan],
            "current_node_id": self.current_node_id,
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "next_action": self.next_action,
            "last_event_sequence": self.last_event_sequence,
        }


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    run_id: str
    sequence: int
    type: DomainEventType
    data: dict[str, Any]
    timestamp: datetime
    schema_version: int = DOMAIN_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("domain_event_id_must_not_be_empty")
        if not self.run_id.strip():
            raise ValueError("domain_event_run_id_must_not_be_empty")
        if self.sequence <= 0:
            raise ValueError("domain_event_sequence_must_be_positive")
        if self.schema_version != DOMAIN_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported_domain_event_schema_version")
        if self.timestamp.tzinfo is None:
            raise ValueError("domain_event_timestamp_must_be_timezone_aware")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        type: DomainEventType,
        data: dict[str, Any],
    ) -> DomainEvent:
        return cls(
            event_id=f"evt-{uuid4().hex}",
            run_id=run_id,
            sequence=sequence,
            type=type,
            data=data,
            timestamp=datetime.now(UTC),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "schema_version": self.schema_version,
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DomainEvent:
        try:
            return cls(
                event_id=str(value["event_id"]),
                run_id=str(value["run_id"]),
                sequence=int(value["sequence"]),
                schema_version=int(value["schema_version"]),
                type=DomainEventType(value["type"]),
                data=dict(value["data"]),
                timestamp=datetime.fromisoformat(value["timestamp"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_domain_event: {error}") from error


def reduce_domain_event(
    state: DurableTaskState | None,
    event: DomainEvent,
) -> DurableTaskState:
    """Apply one domain event without mutating the previous state."""

    if state is None:
        if event.type != DomainEventType.TASK_CREATED:
            raise ValueError("first_domain_event_must_create_task")
        if event.sequence != 1:
            raise ValueError("first_domain_event_sequence_must_be_one")
        task_id = _required_text(event.data, "task_id")
        goal = _required_text(event.data, "goal")
        acceptance_criteria = _text_tuple(event.data.get("acceptance_criteria", []))
        return DurableTaskState(
            run_id=event.run_id,
            task_id=task_id,
            goal=goal,
            acceptance_criteria=acceptance_criteria,
            last_event_sequence=event.sequence,
        )

    _validate_next_event(state, event)
    if event.type == DomainEventType.TASK_CREATED:
        raise ValueError("task_already_created")

    updated = state
    if event.type == DomainEventType.PLAN_ACCEPTED:
        if state.plan:
            raise ValueError("task_plan_already_accepted")
        updated = replace(state, plan=_parse_plan(event.data.get("nodes")))
    elif event.type == DomainEventType.TASK_NODE_STARTED:
        node_id = _required_text(event.data, "node_id")
        if state.current_node_id is not None:
            raise ValueError("another_task_node_is_already_running")
        node = _find_node(state.plan, node_id)
        if node.status != TaskNodeStatus.PENDING:
            raise ValueError("task_node_is_not_pending")
        completed = {
            candidate.id
            for candidate in state.plan
            if candidate.status == TaskNodeStatus.COMPLETED
        }
        if not set(node.dependencies).issubset(completed):
            raise ValueError("task_node_dependencies_not_completed")
        updated = replace(
            state,
            plan=_replace_node(state.plan, replace(node, status=TaskNodeStatus.RUNNING)),
            current_node_id=node_id,
        )
    elif event.type == DomainEventType.TASK_NODE_COMPLETED:
        node_id = _required_text(event.data, "node_id")
        node = _find_node(state.plan, node_id)
        if state.current_node_id != node_id or node.status != TaskNodeStatus.RUNNING:
            raise ValueError("task_node_must_be_running_before_completion")
        receipt_ids = _text_tuple(event.data.get("evidence_receipt_ids", []))
        updated = replace(
            state,
            plan=_replace_node(
                state.plan,
                replace(
                    node,
                    status=TaskNodeStatus.COMPLETED,
                    evidence_receipt_ids=receipt_ids,
                ),
            ),
            current_node_id=None,
            next_action=None,
        )
    elif event.type == DomainEventType.BLOCKER_ADDED:
        blocker = TaskBlocker(
            id=_required_text(event.data, "blocker_id"),
            description=_required_text(event.data, "description"),
        )
        if any(existing.id == blocker.id for existing in state.blockers):
            raise ValueError("task_blocker_already_exists")
        updated = replace(state, blockers=state.blockers + (blocker,))
    elif event.type == DomainEventType.BLOCKER_RESOLVED:
        blocker_id = _required_text(event.data, "blocker_id")
        if not any(blocker.id == blocker_id for blocker in state.blockers):
            raise ValueError("task_blocker_not_found")
        updated = replace(
            state,
            blockers=tuple(
                blocker for blocker in state.blockers if blocker.id != blocker_id
            ),
        )
    elif event.type == DomainEventType.NEXT_ACTION_SET:
        updated = replace(state, next_action=_required_text(event.data, "next_action"))
    else:  # pragma: no cover - exhaustive guard for future enum members.
        raise ValueError(f"unsupported_domain_event_type: {event.type}")

    return replace(updated, last_event_sequence=event.sequence)


def replay_domain_events(events: Iterable[DomainEvent]) -> DurableTaskState:
    state: DurableTaskState | None = None
    for event in events:
        state = reduce_domain_event(state, event)
    if state is None:
        raise ValueError("cannot_replay_empty_domain_event_stream")
    return state


class JsonlDomainEventStore:
    """Minimal single-run append-only store; concurrency is intentionally unsupported."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: DomainEvent) -> bool:
        existing = self.read_all()
        for stored in existing:
            if stored.event_id == event.event_id or stored.sequence == event.sequence:
                if stored.to_dict() == event.to_dict():
                    return False
                raise ValueError("conflicting_domain_event")
        if existing:
            last = existing[-1]
            if event.run_id != last.run_id:
                raise ValueError("event_store_accepts_one_run_only")
            expected_sequence = last.sequence + 1
        else:
            expected_sequence = 1
        if event.sequence != expected_sequence:
            raise ValueError(
                f"domain_event_out_of_order: expected={expected_sequence}, actual={event.sequence}"
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def read_all(self) -> list[DomainEvent]:
        if not self.path.exists():
            return []
        events: list[DomainEvent] = []
        event_ids: set[str] = set()
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    event = DomainEvent.from_dict(raw)
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(
                        f"invalid_domain_event_at_line_{line_number}: {error}"
                    ) from error
                if events:
                    previous = events[-1]
                    if event.run_id != previous.run_id:
                        raise ValueError("event_store_contains_multiple_runs")
                    if event.sequence != previous.sequence + 1:
                        raise ValueError("event_store_contains_non_contiguous_sequences")
                elif event.sequence != 1:
                    raise ValueError("event_store_first_sequence_must_be_one")
                if event.event_id in event_ids:
                    raise ValueError("event_store_contains_duplicate_event_id")
                events.append(event)
                event_ids.add(event.event_id)
        return events

    def read_after(self, sequence: int) -> list[DomainEvent]:
        if sequence < 0:
            raise ValueError("event_sequence_must_not_be_negative")
        return [event for event in self.read_all() if event.sequence > sequence]


def _validate_next_event(state: DurableTaskState, event: DomainEvent) -> None:
    if event.run_id != state.run_id:
        raise ValueError("domain_event_run_id_mismatch")
    expected = state.last_event_sequence + 1
    if event.sequence != expected:
        raise ValueError(
            f"domain_event_sequence_mismatch: expected={expected}, actual={event.sequence}"
        )


def _parse_plan(value: Any) -> tuple[TaskNode, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("accepted_plan_requires_nodes")
    nodes: list[TaskNode] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("task_plan_node_must_be_an_object")
        nodes.append(
            TaskNode(
                id=_required_text(raw, "id"),
                description=_required_text(raw, "description"),
                dependencies=_text_tuple(raw.get("dependencies", [])),
            )
        )
    ids = [node.id for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("task_plan_node_ids_must_be_unique")
    known = set(ids)
    for node in nodes:
        if node.id in node.dependencies:
            raise ValueError("task_plan_node_cannot_depend_on_itself")
        if not set(node.dependencies).issubset(known):
            raise ValueError("task_plan_dependency_not_found")
    _validate_acyclic_plan(nodes)
    return tuple(nodes)


def _validate_acyclic_plan(nodes: list[TaskNode]) -> None:
    dependencies = {node.id: set(node.dependencies) for node in nodes}
    remaining = set(dependencies)
    while remaining:
        ready = {
            node_id
            for node_id in remaining
            if not (dependencies[node_id] & remaining)
        }
        if not ready:
            raise ValueError("task_plan_dependencies_must_be_acyclic")
        remaining -= ready


def _find_node(plan: tuple[TaskNode, ...], node_id: str) -> TaskNode:
    for node in plan:
        if node.id == node_id:
            return node
    raise ValueError("task_plan_node_not_found")


def _replace_node(
    plan: tuple[TaskNode, ...],
    updated: TaskNode,
) -> tuple[TaskNode, ...]:
    return tuple(updated if node.id == updated.id else node for node in plan)


def _required_text(value: dict[str, Any], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{key}_must_be_non_empty_text")
    return raw


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("expected_text_list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("text_list_items_must_be_non_empty")
        result.append(item)
    return tuple(result)
