from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .context import ModelContext
from .events import AgentEvent
from .state import AgentState, ToolExecution


class TraceWriter:
    def __init__(self, root: Path) -> None:
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        self.path = root / run_id
        self.path.mkdir(parents=True, exist_ok=False)

    def write_metadata(self, task: str) -> None:
        self._write_json("metadata.json", {"run_id": self.path.name, "task": task, "started_at": datetime.now(UTC).isoformat()})

    def write_event(self, event: AgentEvent) -> None:
        self._append_jsonl("events.jsonl", event.to_dict())

    def write_context(self, step: int, context: ModelContext, view: str = "model_input") -> None:
        self._append_jsonl("context.jsonl", {"step": step, "view": view, **context.to_dict()})

    def write_tool(self, step: int, execution: ToolExecution) -> None:
        self._append_jsonl("tools.jsonl", {"step": step, **execution.to_dict()})

    def write_result(self, state: AgentState) -> None:
        self._write_json("result.json", state.summary())

    def _write_json(self, name: str, data: dict) -> None:
        (self.path / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_jsonl(self, name: str, data: dict) -> None:
        with (self.path / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
