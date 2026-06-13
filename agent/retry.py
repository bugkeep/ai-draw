import asyncio
import random
from typing import Callable


# ── non-retryable error patterns ───────────────────────────────────────
# These error types should NOT be retried, no matter the attempt.
_NON_RETRYABLE_SUBSTRINGS = [
    "invalid_api_key",
    "authentication",
    "insufficient_quota",
    "model_not_found",
    "content_filter",
    "invalid request",
]


def _is_retryable(error: str) -> bool:
    low = error.lower()
    return not any(s in low for s in _NON_RETRYABLE_SUBSTRINGS)


def backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """Exponential backoff with jitter.

    ``attempt`` is 0-based (first retry = attempt 0 → delay 1s).
    Returns delay in seconds.
    """
    delay = min(base * (2 ** attempt) + random.uniform(0, 0.5), max_delay)
    return delay


async def retry_with_backoff(
    fn: Callable[[], asyncio.Future],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    on_retry: Callable[[int, float, str], asyncio.Future | None] | None = None,
) -> tuple[asyncio.Future, int]:
    """Wrap an async call with exponential-backoff retry.

    Parameters
    ----------
    fn:
        Zero-argument async callable.
    max_retries:
        Max retry attempts before giving up.
    base_delay, max_delay:
        Backoff parameters (see ``backoff_delay``).
    on_retry:
        Optional async callback invoked before each sleep with
        ``(attempt, delay_seconds, error_message)``.

    Returns
    -------
    ``(result, total_attempts)`` — the final result and the number
    of attempts made (1 = no retry, 2+ = retried).
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await fn()
            return result, attempt + 1
        except Exception as e:
            last_exc = e
            if not _is_retryable(str(e)):
                raise
            if attempt == max_retries:
                raise

            delay = backoff_delay(attempt, base_delay, max_delay)
            if on_retry:
                r = on_retry(attempt, delay, str(e))
                if r is not None:
                    await r
            await asyncio.sleep(delay)

    # Should not reach here, but defend the return type:
    raise last_exc  # type: ignore[misc]
