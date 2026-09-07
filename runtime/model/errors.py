from __future__ import annotations

from typing import Any


class ModelProviderError(RuntimeError):
    """A model API request failed before a valid runtime response was produced."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        code: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "detail": self.detail,
        }


class ModelProtocolError(ModelProviderError):
    """The provider returned a completed response that violates our adapter contract."""


class ModelStreamIncompleteError(ModelProviderError):
    """A stream ended before the provider's terminal completion event."""
