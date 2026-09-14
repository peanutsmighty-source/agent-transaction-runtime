from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .durable_contracts import (
    ExecutionReceipt,
    JsonReceiptStore,
    PlanNodeSpec,
    ReceiptKind,
    ReceiptStatus,
    ResumeRequest,
    TaskPlan,
)

DOMAIN_EVENT_SCHEMA_VERSION = 2
CHECKPOINT_SCHEMA_VERSION = 2
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")


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
    acceptance_criteria: tuple[str, ...] = ()
    status: TaskNodeStatus = TaskNodeStatus.PENDING
    evidence_receipt_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "dependencies": list(self.dependencies),
            "acceptance_criteria": list(self.acceptance_criteria),
            "status": self.status.value,
            "evidence_receipt_ids": list(self.evidence_receipt_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskNode:
        try:
            return cls(
                id=_required_text(value, "id"),
                description=_required_text(value, "description"),
                dependencies=_text_tuple(value.get("dependencies", [])),
                acceptance_criteria=_text_tuple(
                    value.get("acceptance_criteria", [])
                ),
                status=TaskNodeStatus(value["status"]),
                evidence_receipt_ids=_text_tuple(
                    value.get("evidence_receipt_ids", [])
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_task_node: {error}") from error


@dataclass(frozen=True)
class TaskBlocker:
    id: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "description": self.description}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskBlocker:
        return cls(
            id=_required_text(value, "id"),
            description=_required_text(value, "description"),
        )


@dataclass(frozen=True)
class DurableTaskState:
    """Materialized task state produced only by reducing domain events."""

    run_id: str
    task_id: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    plan_id: str | None = None
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
            "plan_id": self.plan_id,
            "plan": [node.to_dict() for node in self.plan],
            "current_node_id": self.current_node_id,
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "next_action": self.next_action,
            "last_event_sequence": self.last_event_sequence,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DurableTaskState:
        try:
            plan = value.get("plan", [])
            blockers = value.get("blockers", [])
            if not isinstance(plan, list) or not isinstance(blockers, list):
                raise ValueError("task_state_plan_and_blockers_must_be_lists")
            current_node_id = value.get("current_node_id")
            next_action = value.get("next_action")
            plan_id = value.get("plan_id")
            if plan_id is not None and not isinstance(plan_id, str):
                raise ValueError("plan_id_must_be_text_or_null")
            if current_node_id is not None and not isinstance(current_node_id, str):
                raise ValueError("current_node_id_must_be_text_or_null")
            if next_action is not None and not isinstance(next_action, str):
                raise ValueError("next_action_must_be_text_or_null")
            state = cls(
                run_id=_required_text(value, "run_id"),
                task_id=_required_text(value, "task_id"),
                goal=_required_text(value, "goal"),
                acceptance_criteria=_text_tuple(
                    value.get("acceptance_criteria", [])
                ),
                plan_id=plan_id,
                plan=tuple(TaskNode.from_dict(node) for node in plan),
                current_node_id=current_node_id,
                blockers=tuple(TaskBlocker.from_dict(blocker) for blocker in blockers),
                next_action=next_action,
                last_event_sequence=int(value["last_event_sequence"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_durable_task_state: {error}") from error
        if state.last_event_sequence <= 0:
            raise ValueError("task_state_last_event_sequence_must_be_positive")
        if state.plan:
            if state.plan_id is None:
                raise ValueError("task_state_plan_requires_plan_id")
            TaskPlan(
                plan_id=state.plan_id,
                nodes=tuple(
                    PlanNodeSpec(
                        id=node.id,
                        description=node.description,
                        dependencies=node.dependencies,
                        acceptance_criteria=node.acceptance_criteria,
                    )
                    for node in state.plan
                ),
            )
        elif state.plan_id is not None:
            raise ValueError("task_state_plan_id_requires_plan")
        running_node_ids = tuple(
            node.id for node in state.plan if node.status == TaskNodeStatus.RUNNING
        )
        if len(running_node_ids) > 1:
            raise ValueError("task_state_cannot_have_multiple_running_nodes")
        expected_running = (
            () if state.current_node_id is None else (state.current_node_id,)
        )
        if running_node_ids != expected_running:
            raise ValueError("task_state_current_node_must_match_running_node")
        return state


@dataclass(frozen=True)
class CheckpointMetadata:
    checkpoint_id: str
    run_id: str
    checkpoint_sequence: int
    event_sequence: int
    created_at: datetime
    checksum: str
    workspace_revision: str | None = None
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def to_dict(self, *, include_checksum: bool = True) -> dict[str, Any]:
        value = {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "checkpoint_sequence": self.checkpoint_sequence,
            "event_sequence": self.event_sequence,
            "created_at": self.created_at.isoformat(),
            "workspace_revision": self.workspace_revision,
            "schema_version": self.schema_version,
        }
        if include_checksum:
            value["checksum"] = self.checksum
        return value


@dataclass(frozen=True)
class TaskCheckpoint:
    """Versioned snapshot of materialized task state, not a conversation summary."""

    metadata: CheckpointMetadata
    task_state: DurableTaskState

    @classmethod
    def create(
        cls,
        *,
        task_state: DurableTaskState,
        checkpoint_sequence: int,
        workspace_revision: str | None = None,
        checkpoint_id: str | None = None,
        created_at: datetime | None = None,
    ) -> TaskCheckpoint:
        if checkpoint_sequence <= 0:
            raise ValueError("checkpoint_sequence_must_be_positive")
        metadata = CheckpointMetadata(
            checkpoint_id=checkpoint_id or f"cp-{uuid4().hex}",
            run_id=task_state.run_id,
            checkpoint_sequence=checkpoint_sequence,
            event_sequence=task_state.last_event_sequence,
            created_at=created_at or datetime.now(UTC),
            checksum="",
            workspace_revision=workspace_revision,
        )
        checkpoint = cls(metadata=metadata, task_state=task_state)
        return cls(
            metadata=replace(metadata, checksum=checkpoint.calculate_checksum()),
            task_state=task_state,
        )

    def calculate_checksum(self) -> str:
        canonical = json.dumps(
            {
                "metadata": self.metadata.to_dict(include_checksum=False),
                "task_state": self.task_state.to_dict(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return sha256(canonical).hexdigest()

    def verify(self) -> None:
        if self.metadata.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("unsupported_checkpoint_schema_version")
        if not self.metadata.checkpoint_id.strip():
            raise ValueError("checkpoint_id_must_not_be_empty")
        if self.metadata.checkpoint_sequence <= 0:
            raise ValueError("checkpoint_sequence_must_be_positive")
        if self.metadata.created_at.tzinfo is None:
            raise ValueError("checkpoint_timestamp_must_be_timezone_aware")
        if self.metadata.run_id != self.task_state.run_id:
            raise ValueError("checkpoint_run_id_mismatch")
        if self.metadata.event_sequence != self.task_state.last_event_sequence:
            raise ValueError("checkpoint_event_sequence_mismatch")
        if self.metadata.checksum != self.calculate_checksum():
            raise ValueError("checkpoint_checksum_mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "task_state": self.task_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskCheckpoint:
        try:
            raw_metadata = value["metadata"]
            if not isinstance(raw_metadata, dict):
                raise ValueError("checkpoint_metadata_must_be_an_object")
            workspace_revision = raw_metadata.get("workspace_revision")
            if workspace_revision is not None and not isinstance(
                workspace_revision, str
            ):
                raise ValueError("workspace_revision_must_be_text_or_null")
            metadata = CheckpointMetadata(
                checkpoint_id=_required_text(raw_metadata, "checkpoint_id"),
                run_id=_required_text(raw_metadata, "run_id"),
                checkpoint_sequence=int(raw_metadata["checkpoint_sequence"]),
                event_sequence=int(raw_metadata["event_sequence"]),
                created_at=datetime.fromisoformat(raw_metadata["created_at"]),
                checksum=_required_text(raw_metadata, "checksum"),
                workspace_revision=workspace_revision,
                schema_version=int(raw_metadata["schema_version"]),
            )
            raw_state = value["task_state"]
            if not isinstance(raw_state, dict):
                raise ValueError("checkpoint_task_state_must_be_an_object")
            checkpoint = cls(
                metadata=metadata,
                task_state=DurableTaskState.from_dict(raw_state),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_task_checkpoint: {error}") from error
        checkpoint.verify()
        return checkpoint


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
        plan = TaskPlan.from_dict(event.data)
        updated = replace(
            state,
            plan_id=plan.plan_id,
            plan=tuple(
                TaskNode(
                    id=node.id,
                    description=node.description,
                    dependencies=node.dependencies,
                    acceptance_criteria=node.acceptance_criteria,
                )
                for node in plan.nodes
            ),
        )
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


class JsonTaskCheckpointStore:
    """Atomic-file checkpoint store for one run; concurrent writers are unsupported."""

    def __init__(self, root: Path, run_id: str) -> None:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("checkpoint_run_id_contains_unsafe_characters")
        self.root = root
        self.run_id = run_id
        self.run_path = root / run_id

    def save(self, checkpoint: TaskCheckpoint) -> bool:
        checkpoint.verify()
        if checkpoint.metadata.run_id != self.run_id:
            raise ValueError("checkpoint_store_run_id_mismatch")
        self.run_path.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(checkpoint.metadata.checkpoint_sequence)
        serialized = json.dumps(
            checkpoint.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        )
        if destination.exists():
            existing = self._read_path(destination)
            if existing == checkpoint:
                return False
            raise ValueError("conflicting_checkpoint_sequence")

        temporary = self.run_path / f".{destination.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return True

    def load_latest(self) -> TaskCheckpoint:
        paths = sorted(self.run_path.glob("checkpoint-*.json"))
        if not paths:
            raise ValueError("checkpoint_not_found")
        checkpoint = self._read_path(paths[-1])
        if checkpoint.metadata.run_id != self.run_id:
            raise ValueError("checkpoint_store_run_id_mismatch")
        return checkpoint

    def load_latest_or_none(self) -> TaskCheckpoint | None:
        try:
            return self.load_latest()
        except ValueError as error:
            if str(error) == "checkpoint_not_found":
                return None
            raise

    def _path_for(self, sequence: int) -> Path:
        return self.run_path / f"checkpoint-{sequence:08d}.json"

    @staticmethod
    def _read_path(path: Path) -> TaskCheckpoint:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid_checkpoint_file: {error}") from error
        if not isinstance(raw, dict):
            raise ValueError("invalid_checkpoint_file: root_must_be_an_object")
        return TaskCheckpoint.from_dict(raw)


def replay_from_checkpoint(
    checkpoint: TaskCheckpoint,
    later_events: Iterable[DomainEvent],
) -> DurableTaskState:
    """Resume a snapshot by applying only events after its covered sequence."""

    checkpoint.verify()
    state = checkpoint.task_state
    for event in later_events:
        if event.sequence <= checkpoint.metadata.event_sequence:
            raise ValueError("checkpoint_replay_received_already_applied_event")
        state = reduce_domain_event(state, event)
    return state


class DurableTaskSession:
    """Validates by reducing, persists the event, then publishes the new state."""

    def __init__(
        self,
        *,
        event_store: JsonlDomainEventStore,
        checkpoint_store: JsonTaskCheckpointStore,
        receipt_store: JsonReceiptStore,
        state: DurableTaskState,
    ) -> None:
        if state.run_id != checkpoint_store.run_id:
            raise ValueError("durable_session_run_id_mismatch")
        if state.run_id != receipt_store.run_id:
            raise ValueError("durable_session_receipt_store_run_id_mismatch")
        self.event_store = event_store
        self.checkpoint_store = checkpoint_store
        self.receipt_store = receipt_store
        self.state = state

    @classmethod
    def create(
        cls,
        *,
        event_store: JsonlDomainEventStore,
        checkpoint_store: JsonTaskCheckpointStore,
        receipt_store: JsonReceiptStore,
        task_id: str,
        goal: str,
        acceptance_criteria: list[str] | None = None,
    ) -> DurableTaskSession:
        if event_store.read_all():
            raise ValueError("durable_session_event_store_must_be_empty")
        created = DomainEvent.create(
            run_id=checkpoint_store.run_id,
            sequence=1,
            type=DomainEventType.TASK_CREATED,
            data={
                "task_id": task_id,
                "goal": goal,
                "acceptance_criteria": acceptance_criteria or [],
            },
        )
        state = reduce_domain_event(None, created)
        event_store.append(created)
        return cls(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
            receipt_store=receipt_store,
            state=state,
        )

    @classmethod
    def resume(
        cls,
        *,
        event_store: JsonlDomainEventStore,
        checkpoint_store: JsonTaskCheckpointStore,
        receipt_store: JsonReceiptStore,
        request: ResumeRequest,
    ) -> DurableTaskSession:
        if request.run_id != checkpoint_store.run_id:
            raise ValueError("resume_request_checkpoint_store_run_id_mismatch")
        if request.run_id != receipt_store.run_id:
            raise ValueError("resume_request_receipt_store_run_id_mismatch")
        events = event_store.read_all()
        if not events:
            raise ValueError("cannot_resume_empty_domain_event_stream")
        if request.run_id != events[0].run_id:
            raise ValueError("resume_request_event_store_run_id_mismatch")
        checkpoint = checkpoint_store.load_latest_or_none()
        if checkpoint is None:
            if request.require_checkpoint:
                raise ValueError("resume_request_requires_checkpoint")
            if request.workspace_revision is not None:
                raise ValueError("workspace_revision_requires_checkpoint")
            state = replay_domain_events(events)
        else:
            if checkpoint.metadata.run_id != events[0].run_id:
                raise ValueError("checkpoint_event_store_run_id_mismatch")
            if checkpoint.metadata.event_sequence > events[-1].sequence:
                raise ValueError("checkpoint_is_ahead_of_event_store")
            if (
                request.workspace_revision is not None
                and checkpoint.metadata.workspace_revision
                != request.workspace_revision
            ):
                raise ValueError("resume_workspace_revision_mismatch")
            state = replay_from_checkpoint(
                checkpoint,
                (
                    event
                    for event in events
                    if event.sequence > checkpoint.metadata.event_sequence
                ),
            )
        if state.task_id != request.task_id:
            raise ValueError("resume_request_task_id_mismatch")
        _validate_state_receipts(state, receipt_store)
        return cls(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
            receipt_store=receipt_store,
            state=state,
        )

    def accept_plan(self, plan: TaskPlan) -> DomainEvent:
        return self.record(DomainEventType.PLAN_ACCEPTED, plan.to_dict())

    def start_node(self, node_id: str) -> DomainEvent:
        return self.record(DomainEventType.TASK_NODE_STARTED, {"node_id": node_id})

    def complete_node(
        self,
        node_id: str,
        *,
        evidence_receipts: tuple[ExecutionReceipt, ...] = (),
    ) -> DomainEvent:
        for receipt in evidence_receipts:
            _validate_receipt_for_node(self.state, node_id, receipt)
        _validate_node_acceptance_receipts(self.state, node_id, evidence_receipts)
        event = DomainEvent.create(
            run_id=self.state.run_id,
            sequence=self.state.last_event_sequence + 1,
            type=DomainEventType.TASK_NODE_COMPLETED,
            data={
                "node_id": node_id,
                "evidence_receipt_ids": [
                    receipt.receipt_id for receipt in evidence_receipts
                ],
            },
        )
        next_state = reduce_domain_event(self.state, event)
        for receipt in evidence_receipts:
            self.receipt_store.save(receipt)
        self.event_store.append(event)
        self.state = next_state
        return event

    def add_blocker(self, blocker_id: str, description: str) -> DomainEvent:
        return self.record(
            DomainEventType.BLOCKER_ADDED,
            {"blocker_id": blocker_id, "description": description},
        )

    def resolve_blocker(self, blocker_id: str) -> DomainEvent:
        return self.record(
            DomainEventType.BLOCKER_RESOLVED,
            {"blocker_id": blocker_id},
        )

    def set_next_action(self, next_action: str) -> DomainEvent:
        return self.record(
            DomainEventType.NEXT_ACTION_SET,
            {"next_action": next_action},
        )

    def record(
        self,
        type: DomainEventType,
        data: dict[str, Any],
    ) -> DomainEvent:
        event = DomainEvent.create(
            run_id=self.state.run_id,
            sequence=self.state.last_event_sequence + 1,
            type=type,
            data=data,
        )
        next_state = reduce_domain_event(self.state, event)
        self.event_store.append(event)
        self.state = next_state
        return event

    def save_checkpoint(self, workspace_revision: str | None = None) -> TaskCheckpoint:
        latest = self.checkpoint_store.load_latest_or_none()
        checkpoint_sequence = (
            latest.metadata.checkpoint_sequence + 1 if latest is not None else 1
        )
        checkpoint = TaskCheckpoint.create(
            task_state=self.state,
            checkpoint_sequence=checkpoint_sequence,
            workspace_revision=workspace_revision,
        )
        self.checkpoint_store.save(checkpoint)
        return checkpoint


def _validate_next_event(state: DurableTaskState, event: DomainEvent) -> None:
    if event.run_id != state.run_id:
        raise ValueError("domain_event_run_id_mismatch")
    expected = state.last_event_sequence + 1
    if event.sequence != expected:
        raise ValueError(
            f"domain_event_sequence_mismatch: expected={expected}, actual={event.sequence}"
        )


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


def _validate_receipt_for_node(
    state: DurableTaskState,
    node_id: str,
    receipt: ExecutionReceipt,
) -> None:
    if receipt.run_id != state.run_id:
        raise ValueError("receipt_run_id_mismatch")
    if receipt.task_id != state.task_id:
        raise ValueError("receipt_task_id_mismatch")
    if receipt.node_id != node_id:
        raise ValueError("receipt_node_id_mismatch")
    if receipt.status != ReceiptStatus.SUCCEEDED:
        raise ValueError("failed_receipt_cannot_complete_task_node")


def _validate_node_acceptance_receipts(
    state: DurableTaskState,
    node_id: str,
    receipts: tuple[ExecutionReceipt, ...],
) -> None:
    node = _find_node(state.plan, node_id)
    if node.acceptance_criteria and not any(
        receipt.kind == ReceiptKind.VERIFICATION for receipt in receipts
    ):
        raise ValueError("task_node_acceptance_requires_verification_receipt")


def _validate_state_receipts(
    state: DurableTaskState,
    receipt_store: JsonReceiptStore,
) -> None:
    for node in state.plan:
        receipts: list[ExecutionReceipt] = []
        for receipt_id in node.evidence_receipt_ids:
            receipt = receipt_store.load(receipt_id)
            _validate_receipt_for_node(state, node.id, receipt)
            receipts.append(receipt)
        if node.status == TaskNodeStatus.COMPLETED:
            _validate_node_acceptance_receipts(state, node.id, tuple(receipts))


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
