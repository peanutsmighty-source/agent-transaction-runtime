from __future__ import annotations

from dataclasses import asdict, dataclass

from ..model.errors import ModelProviderError


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    failed_attempt: int
    next_attempt: int | None
    delay_seconds: float
    reason: str
    estimated_input_tokens_per_attempt: int
    estimated_cumulative_input_tokens: int

    def to_dict(self) -> dict:
        return asdict(self)


class ModelRetryPolicy:
    """Decide whether an uncommitted model request may be attempted again."""

    def __init__(
        self,
        max_attempts: int = 1,
        *,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 4.0,
        max_estimated_input_tokens: int | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("retry_max_attempts_must_be_at_least_one")
        if base_delay_seconds < 0:
            raise ValueError("retry_base_delay_must_not_be_negative")
        if max_delay_seconds < 0:
            raise ValueError("retry_max_delay_must_not_be_negative")
        if max_estimated_input_tokens is not None and max_estimated_input_tokens < 1:
            raise ValueError("retry_token_budget_must_be_positive")
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.max_estimated_input_tokens = max_estimated_input_tokens

    def evaluate(
        self,
        error: ModelProviderError,
        *,
        failed_attempt: int,
        estimated_input_tokens: int,
    ) -> RetryDecision:
        next_attempt = failed_attempt + 1
        cumulative = estimated_input_tokens * next_attempt
        if error.retryable is not True:
            return self._stop(
                failed_attempt,
                estimated_input_tokens,
                cumulative,
                "provider_marked_non_retryable",
            )
        if failed_attempt >= self.max_attempts:
            return self._stop(
                failed_attempt,
                estimated_input_tokens,
                estimated_input_tokens * failed_attempt,
                "max_attempts_exhausted",
            )
        if (
            self.max_estimated_input_tokens is not None
            and cumulative > self.max_estimated_input_tokens
        ):
            return self._stop(
                failed_attempt,
                estimated_input_tokens,
                estimated_input_tokens * failed_attempt,
                "estimated_input_token_budget_exhausted",
            )

        delay = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (failed_attempt - 1)),
        )
        return RetryDecision(
            should_retry=True,
            failed_attempt=failed_attempt,
            next_attempt=next_attempt,
            delay_seconds=delay,
            reason="retryable_model_request_failed",
            estimated_input_tokens_per_attempt=estimated_input_tokens,
            estimated_cumulative_input_tokens=cumulative,
        )

    @staticmethod
    def _stop(
        failed_attempt: int,
        estimated_input_tokens: int,
        cumulative: int,
        reason: str,
    ) -> RetryDecision:
        return RetryDecision(
            should_retry=False,
            failed_attempt=failed_attempt,
            next_attempt=None,
            delay_seconds=0,
            reason=reason,
            estimated_input_tokens_per_attempt=estimated_input_tokens,
            estimated_cumulative_input_tokens=cumulative,
        )
