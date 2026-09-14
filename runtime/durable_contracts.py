from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4


PLAN_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class PlanNodeSpec:
    """A planned unit of work before runtime progress is attached to it."""

    id: str
    description: str
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_safe_identifier(self.id, "plan_node_id")
        _require_text(self.description, "plan_node_description")
        _validate_text_tuple(self.dependencies, "plan_node_dependencies")
        _validate_text_tuple(
            self.acceptance_criteria, "plan_node_acceptance_criteria"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "dependencies": list(self.dependencies),
            "acceptance_criteria": list(self.acceptance_criteria),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PlanNodeSpec:
        try:
            return cls(
                id=value["id"],
                description=value["description"],
                dependencies=_parse_text_tuple(
                    value.get("dependencies", []), "plan_node_dependencies"
                ),
                acceptance_criteria=_parse_text_tuple(
                    value.get("acceptance_criteria", []),
                    "plan_node_acceptance_criteria",
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_plan_node_spec: {error}") from error


@dataclass(frozen=True)
class TaskPlan:
    """Versioned task graph accepted before nodes can start."""

    plan_id: str
    nodes: tuple[PlanNodeSpec, ...]
    schema_version: int = PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_safe_identifier(self.plan_id, "plan_id")
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported_plan_schema_version")
        if not isinstance(self.nodes, tuple):
            raise ValueError("task_plan_nodes_must_be_a_tuple")
        if not self.nodes:
            raise ValueError("task_plan_requires_nodes")
        if not all(isinstance(node, PlanNodeSpec) for node in self.nodes):
            raise ValueError("task_plan_nodes_must_be_plan_node_specs")
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("task_plan_node_ids_must_be_unique")
        known = set(ids)
        for node in self.nodes:
            if node.id in node.dependencies:
                raise ValueError("task_plan_node_cannot_depend_on_itself")
            if not set(node.dependencies).issubset(known):
                raise ValueError("task_plan_dependency_not_found")
        _validate_acyclic_dependencies(self.nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "schema_version": self.schema_version,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskPlan:
        try:
            raw_nodes = value["nodes"]
            if not isinstance(raw_nodes, list):
                raise ValueError("task_plan_nodes_must_be_a_list")
            if not all(isinstance(node, dict) for node in raw_nodes):
                raise ValueError("task_plan_node_must_be_an_object")
            return cls(
                plan_id=value["plan_id"],
                schema_version=int(value["schema_version"]),
                nodes=tuple(PlanNodeSpec.from_dict(node) for node in raw_nodes),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_task_plan: {error}") from error


class ReceiptKind(StrEnum):
    TOOL_EXECUTION = "tool_execution"
    VERIFICATION = "verification"
    WORKSPACE_CHANGE = "workspace_change"
    EXTERNAL_ACTION = "external_action"


class ReceiptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionReceipt:
    """Durable evidence that a concrete operation produced an observable result."""

    receipt_id: str
    run_id: str
    task_id: str
    node_id: str
    kind: ReceiptKind
    status: ReceiptStatus
    summary: str
    evidence_refs: tuple[str, ...]
    created_at: datetime
    checksum: str = ""
    idempotency_key: str | None = None
    external_resource_id: str | None = None
    schema_version: int = RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_safe_identifier(self.receipt_id, "receipt_id")
        _require_safe_identifier(self.run_id, "receipt_run_id")
        _require_safe_identifier(self.task_id, "receipt_task_id")
        _require_safe_identifier(self.node_id, "receipt_node_id")
        _require_text(self.summary, "receipt_summary")
        if not isinstance(self.kind, ReceiptKind):
            raise ValueError("receipt_kind_must_be_valid")
        if not isinstance(self.status, ReceiptStatus):
            raise ValueError("receipt_status_must_be_valid")
        _validate_text_tuple(self.evidence_refs, "receipt_evidence_refs")
        if not self.evidence_refs:
            raise ValueError("receipt_requires_evidence_ref")
        if self.created_at.tzinfo is None:
            raise ValueError("receipt_timestamp_must_be_timezone_aware")
        if self.schema_version != RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported_receipt_schema_version")
        if self.checksum and self.checksum != self.calculate_checksum():
            raise ValueError("receipt_checksum_mismatch")
        if self.idempotency_key is not None:
            _require_text(self.idempotency_key, "receipt_idempotency_key")
        if self.external_resource_id is not None:
            _require_text(self.external_resource_id, "receipt_external_resource_id")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        task_id: str,
        node_id: str,
        kind: ReceiptKind,
        status: ReceiptStatus,
        summary: str,
        evidence_refs: tuple[str, ...],
        idempotency_key: str | None = None,
        external_resource_id: str | None = None,
        receipt_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ExecutionReceipt:
        receipt = cls(
            receipt_id=receipt_id or f"receipt-{uuid4().hex}",
            run_id=run_id,
            task_id=task_id,
            node_id=node_id,
            kind=kind,
            status=status,
            summary=summary,
            evidence_refs=evidence_refs,
            created_at=created_at or datetime.now(UTC),
            idempotency_key=idempotency_key,
            external_resource_id=external_resource_id,
        )
        return replace(receipt, checksum=receipt.calculate_checksum())

    def calculate_checksum(self) -> str:
        canonical = json.dumps(
            self.to_dict(include_checksum=False),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return sha256(canonical).hexdigest()

    def verify(self) -> None:
        if not self.checksum:
            raise ValueError("receipt_checksum_must_not_be_empty")
        if self.checksum != self.calculate_checksum():
            raise ValueError("receipt_checksum_mismatch")

    def to_dict(self, *, include_checksum: bool = True) -> dict[str, Any]:
        value = {
            "receipt_id": self.receipt_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at.isoformat(),
            "idempotency_key": self.idempotency_key,
            "external_resource_id": self.external_resource_id,
            "schema_version": self.schema_version,
        }
        if include_checksum:
            value["checksum"] = self.checksum
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionReceipt:
        try:
            receipt = cls(
                receipt_id=value["receipt_id"],
                run_id=value["run_id"],
                task_id=value["task_id"],
                node_id=value["node_id"],
                kind=ReceiptKind(value["kind"]),
                status=ReceiptStatus(value["status"]),
                summary=value["summary"],
                evidence_refs=_parse_text_tuple(
                    value["evidence_refs"], "receipt_evidence_refs"
                ),
                created_at=datetime.fromisoformat(value["created_at"]),
                checksum=value["checksum"],
                idempotency_key=value.get("idempotency_key"),
                external_resource_id=value.get("external_resource_id"),
                schema_version=int(value["schema_version"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid_execution_receipt: {error}") from error
        receipt.verify()
        return receipt


class JsonReceiptStore:
    """Atomic immutable receipt files scoped to one run."""

    def __init__(self, root: Path, run_id: str) -> None:
        _require_safe_identifier(run_id, "receipt_store_run_id")
        self.root = root
        self.run_id = run_id
        self.run_path = root / run_id

    def save(self, receipt: ExecutionReceipt) -> bool:
        receipt.verify()
        if receipt.run_id != self.run_id:
            raise ValueError("receipt_store_run_id_mismatch")
        self.run_path.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(receipt.receipt_id)
        serialized = json.dumps(
            receipt.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        )
        if destination.exists():
            existing = self._read_path(destination)
            if existing == receipt:
                return False
            raise ValueError("conflicting_receipt_id")

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

    def load(self, receipt_id: str) -> ExecutionReceipt:
        receipt = self._read_path(self._path_for(receipt_id))
        if receipt.run_id != self.run_id:
            raise ValueError("receipt_store_run_id_mismatch")
        return receipt

    def _path_for(self, receipt_id: str) -> Path:
        _require_safe_identifier(receipt_id, "receipt_id")
        return self.run_path / f"{receipt_id}.json"

    @staticmethod
    def _read_path(path: Path) -> ExecutionReceipt:
        if not path.exists():
            raise ValueError("receipt_not_found")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid_receipt_file: {error}") from error
        if not isinstance(raw, dict):
            raise ValueError("invalid_receipt_file: root_must_be_an_object")
        return ExecutionReceipt.from_dict(raw)


@dataclass(frozen=True)
class ResumeRequest:
    """Caller-supplied identity and compatibility expectations for recovery."""

    run_id: str
    task_id: str
    workspace_revision: str | None = None
    require_checkpoint: bool = False

    def __post_init__(self) -> None:
        _require_safe_identifier(self.run_id, "resume_run_id")
        _require_safe_identifier(self.task_id, "resume_task_id")
        if not isinstance(self.require_checkpoint, bool):
            raise ValueError("resume_require_checkpoint_must_be_boolean")
        if self.workspace_revision is not None:
            _require_text(self.workspace_revision, "resume_workspace_revision")


def _validate_acyclic_dependencies(nodes: tuple[PlanNodeSpec, ...]) -> None:
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


def _require_safe_identifier(value: Any, name: str) -> str:
    text = _require_text(value, name)
    if not _SAFE_IDENTIFIER.fullmatch(text):
        raise ValueError(f"{name}_contains_unsafe_characters")
    return text


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_must_be_non_empty_text")
    return value


def _validate_text_tuple(value: Any, name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{name}_must_be_a_tuple")
    for item in value:
        _require_text(item, f"{name}_item")


def _parse_text_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name}_must_be_a_list")
    result = tuple(value)
    _validate_text_tuple(result, name)
    return result
