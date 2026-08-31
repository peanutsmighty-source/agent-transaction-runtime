from __future__ import annotations

import json
from dataclasses import dataclass

from ..state import ToolExecution


@dataclass(frozen=True)
class LoopWarning:
    fingerprint: str
    repetitions: int


class RepeatedFailedActionDetector:
    """Detect consecutive identical failed tool calls without deciding how to stop them."""

    def __init__(self, threshold: int = 3) -> None:
        if threshold < 2:
            raise ValueError("loop_detection_threshold_must_be_at_least_two")
        self.threshold = threshold

    def detect(self, history: list[ToolExecution]) -> LoopWarning | None:
        if len(history) < self.threshold:
            return None

        recent = history[-self.threshold :]
        if any(execution.result["success"] for execution in recent):
            return None

        fingerprint = self._fingerprint(recent[0])
        if all(self._fingerprint(execution) == fingerprint for execution in recent[1:]):
            return LoopWarning(fingerprint=fingerprint, repetitions=self.threshold)
        return None

    @staticmethod
    def _fingerprint(execution: ToolExecution) -> str:
        normalized_arguments = json.dumps(execution.arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
        return f"{execution.name}:{normalized_arguments}"
