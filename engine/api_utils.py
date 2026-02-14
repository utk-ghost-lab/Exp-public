"""Shared utilities for Anthropic API calls, including retry for transient errors (529, overloaded)."""

import logging
import time

logger = logging.getLogger(__name__)

# Backoff delays in seconds: 5s, 10s, 20s
RETRY_DELAYS = [5, 10, 20]
MAX_RETRIES = 3


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if the exception indicates a transient error worth retrying."""
    msg = str(exc).lower()
    return (
        "529" in str(exc)
        or "overloaded" in msg
        or "rate_limit" in msg
        or "rate limit" in msg
    )


def messages_create_with_retry(client, **kwargs):
    """Call client.messages.create with retry on 529/overloaded/rate_limit.

    Uses exponential backoff (5s, 10s, 20s). Re-raises the last exception if all retries fail.
    """
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES and _is_retryable_error(e):
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.warning(
                    "Anthropic API transient error (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    str(e)[:200],
                    delay,
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc
