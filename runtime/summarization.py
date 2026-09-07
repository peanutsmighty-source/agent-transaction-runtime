from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from .context import ContextItem


@dataclass(frozen=True)
class SummaryRequest:
    """The exact older context selected for one summary operation."""

    items: tuple[ContextItem, ...]
    max_tokens: int


class Summarizer(Protocol):
    async def summarize(self, request: SummaryRequest) -> str:
        """Return a task-relevant natural-language summary of request.items."""


class FakeSummarizer:
    """Deterministic summarizer used to test compaction without a provider call."""

    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._outcomes = deque(outcomes)
        self.requests: list[SummaryRequest] = []

    async def summarize(self, request: SummaryRequest) -> str:
        self.requests.append(request)
        if not self._outcomes:
            raise RuntimeError("fake_summarizer_outcomes_exhausted")
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
