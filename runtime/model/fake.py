from __future__ import annotations

from collections import deque

from ..context import ModelContext
from .response import ModelResponse


class FakeModelClient:
    """Deterministic model used to test the runtime without an API call."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelContext] = []

    async def generate(self, context: ModelContext, tools: list[dict]) -> ModelResponse:
        self.requests.append(context)
        if not self._responses:
            raise RuntimeError("fake_model_responses_exhausted")
        return self._responses.popleft()
