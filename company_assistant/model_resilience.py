import asyncio
import logging
import os
import random
import time
from collections import deque
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger(__name__)

RATE_LIMITED = (
    "The model service is rate limited right now. Please wait about a minute and ask again."
)
QUOTA_EXHAUSTED = (
    "The model provider's monthly usage limit for this API key has been reached. "
    "Use a production Cohere key or wait for the monthly reset, then ask again."
)
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
TRANSIENT_NAMES = frozenset(
    {"Timeout", "APIConnectionError", "ServiceUnavailableError", "InternalServerError"}
)
RETRY_DELAYS = (6.0, 15.0, 30.0)
RATE_WINDOW = 60.0
MODEL_DEADLINE = 120


def status_of(error: BaseException) -> int | None:
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def is_rate_limit(error: BaseException | None) -> bool:
    return error is not None and (
        status_of(error) == 429 or type(error).__name__ == "RateLimitError"
    )


def is_quota_exhausted(error: BaseException | None) -> bool:
    """A rate-limit error caused by a monthly cap, which waiting a minute cannot clear."""
    if not is_rate_limit(error):
        return False
    text = str(error).lower()
    return any(marker in text for marker in ("/ month", "per month", "monthly"))


def provider_failure_message(error: BaseException | None) -> str | None:
    """User-facing text for a provider limit, or None for any other failure."""
    if is_quota_exhausted(error):
        return QUOTA_EXHAUSTED
    return RATE_LIMITED if is_rate_limit(error) else None


def is_transient(error: BaseException) -> bool:
    """True for provider conditions that usually clear on their own."""
    if is_quota_exhausted(error):
        return False
    return (
        isinstance(error, (TimeoutError, ConnectionError))
        or is_rate_limit(error)
        or status_of(error) in TRANSIENT_STATUS
        or type(error).__name__ in TRANSIENT_NAMES
    )


def retry_delay(error: BaseException, attempt: int) -> float:
    headers = getattr(getattr(error, "response", None), "headers", None)
    try:
        advised = float(headers.get("retry-after")) if headers else None
    except (AttributeError, TypeError, ValueError):
        advised = None
    base = advised if advised is not None else RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
    return min(max(base, 1.0), 60.0) + random.uniform(0.0, 1.0)


def calls_per_minute() -> int:
    try:
        return max(0, int(os.environ.get("MODEL_CALLS_PER_MINUTE", "0")))
    except ValueError:
        return 0


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _now() -> float:
    return time.monotonic()


class CallLimiter:
    """Sliding-window limiter shared by every model call in the process."""

    def __init__(self) -> None:
        self._times: deque[float] = deque()

    async def acquire(self, limit: int) -> None:
        if limit <= 0:
            return
        while True:
            now = _now()
            while self._times and now - self._times[0] >= RATE_WINDOW:
                self._times.popleft()
            if len(self._times) < limit:
                self._times.append(now)
                return
            await _sleep(RATE_WINDOW - (now - self._times[0]) + 0.05)


LIMITER = CallLimiter()


class ResilientLiteLlm(LiteLlm):
    """LiteLLM model that paces calls and retries transient provider errors, centrally.

    Every model use (scope guards, planner, coordinator, specialists) goes through this class, so
    provider limits are handled in one place. Output that was already streamed is never retried.
    """

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        for attempt in range(len(RETRY_DELAYS) + 1):
            await LIMITER.acquire(calls_per_minute())
            started = False
            try:
                async for response in super().generate_content_async(llm_request, stream=stream):
                    started = True
                    yield response
                return
            except Exception as error:
                if started or not is_transient(error) or attempt == len(RETRY_DELAYS):
                    raise
                delay = retry_delay(error, attempt)
                logger.warning(
                    "Model call hit a transient error; retrying: error_type=%s attempt=%d wait=%.0fs",
                    type(error).__name__,
                    attempt + 1,
                    delay,
                )
                await _sleep(delay)


def build_model(name: str, **options: Any) -> ResilientLiteLlm:
    return ResilientLiteLlm(model=name, num_retries=0, **options)
