from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from company_assistant import model_resilience
from company_assistant.model_resilience import (
    QUOTA_EXHAUSTED,
    RATE_LIMITED,
    CallLimiter,
    ResilientLiteLlm,
    is_quota_exhausted,
    is_rate_limit,
    is_transient,
    provider_failure_message,
    retry_delay,
)
from company_assistant.planning import PLAN_UNAVAILABLE, RequestPlanner
from company_assistant.security.topic_guard import SCOPE_UNAVAILABLE, TopicGuard

pytestmark = pytest.mark.integration


class RateLimitError(Exception):
    status_code = 429


class ServerError(Exception):
    status_code = 503


def reply(text="ok"):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(model_resilience, "_sleep", fake_sleep)
    return recorded


def patch_provider(monkeypatch, behaviours):
    """Make the parent provider call replay one behaviour per attempt."""
    calls = []

    async def provider(self, llm_request, stream=False):
        behaviour = behaviours[len(calls)]
        calls.append(behaviour)
        for item in behaviour:
            if isinstance(item, Exception):
                raise item
            yield item

    monkeypatch.setattr(LiteLlm, "generate_content_async", provider)
    return calls


async def collect(model):
    request = LlmRequest(model=model.model, contents=[])
    return [response async for response in model.generate_content_async(request)]


def resilient():
    return ResilientLiteLlm(model="cohere_chat/test-model", num_retries=0)


async def test_rate_limit_is_retried_with_backoff_then_succeeds(monkeypatch, sleeps):
    calls = patch_provider(monkeypatch, [[RateLimitError()], [RateLimitError()], [reply("done")]])
    responses = await collect(resilient())
    assert [r.content.parts[0].text for r in responses] == ["done"]
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert 6 <= sleeps[0] < 8
    assert 15 <= sleeps[1] < 17


async def test_gives_up_after_the_retry_budget(monkeypatch, sleeps):
    attempts = len(model_resilience.RETRY_DELAYS) + 1
    calls = patch_provider(monkeypatch, [[RateLimitError()] for _ in range(attempts)])
    with pytest.raises(RateLimitError):
        await collect(resilient())
    assert len(calls) == attempts
    assert len(sleeps) == attempts - 1


async def test_non_transient_errors_are_not_retried(monkeypatch, sleeps):
    calls = patch_provider(monkeypatch, [[RuntimeError("bad request")]])
    with pytest.raises(RuntimeError):
        await collect(resilient())
    assert len(calls) == 1
    assert sleeps == []


async def test_output_already_streamed_is_never_retried(monkeypatch, sleeps):
    calls = patch_provider(monkeypatch, [[reply("partial"), ServerError()], [reply("again")]])
    request = LlmRequest(model="cohere_chat/test-model", contents=[])
    seen = []
    with pytest.raises(ServerError):
        async for response in resilient().generate_content_async(request):
            seen.append(response)
    assert len(seen) == 1
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error,transient,rate_limit",
    [
        (RateLimitError(), True, True),
        (ServerError(), True, False),
        (TimeoutError(), True, False),
        (ConnectionError(), True, False),
        (RuntimeError("x"), False, False),
        (ValueError("x"), False, False),
    ],
)
def test_error_classification(error, transient, rate_limit):
    assert is_transient(error) is transient
    assert is_rate_limit(error) is rate_limit


def test_retry_after_header_is_honored_and_bounded():
    advised = RateLimitError()
    advised.response = SimpleNamespace(headers={"retry-after": "12"})
    assert 12 <= retry_delay(advised, 0) < 13
    huge = RateLimitError()
    huge.response = SimpleNamespace(headers={"retry-after": "9999"})
    assert retry_delay(huge, 0) < 61
    junk = RateLimitError()
    junk.response = SimpleNamespace(headers={"retry-after": "soon"})
    assert 6 <= retry_delay(junk, 0) < 7


async def test_limiter_spends_the_quota_instead_of_failing(monkeypatch):
    clock = {"now": 100.0}
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(model_resilience, "_now", lambda: clock["now"])
    monkeypatch.setattr(model_resilience, "_sleep", fake_sleep)
    limiter = CallLimiter()
    for _ in range(3):
        await limiter.acquire(3)
    assert waits == []
    await limiter.acquire(3)
    assert len(waits) == 1
    assert 59.9 < waits[0] < 60.2
    await limiter.acquire(0)
    assert len(waits) == 1


def test_calls_per_minute_setting(monkeypatch):
    monkeypatch.delenv("MODEL_CALLS_PER_MINUTE", raising=False)
    assert model_resilience.calls_per_minute() == 0
    monkeypatch.setenv("MODEL_CALLS_PER_MINUTE", "18")
    assert model_resilience.calls_per_minute() == 18
    for bad in ("-5", "many", ""):
        monkeypatch.setenv("MODEL_CALLS_PER_MINUTE", bad)
        assert model_resilience.calls_per_minute() == 0


class FailingModel:
    model = "cohere_chat/test-model"

    def __init__(self, error):
        self.error = error

    async def generate_content_async(self, request, stream=False):
        raise self.error
        yield


def callback_context(question="how many employees are there"):
    return SimpleNamespace(
        agent_name="company_assistant",
        invocation_id="inv",
        user_content=types.Content(role="user", parts=[types.Part(text=question)]),
        state={},
    )


@pytest.mark.parametrize(
    "error,expected_text",
    [
        (RateLimitError(), RATE_LIMITED),
        (RuntimeError("provider detail"), SCOPE_UNAVAILABLE),
        (ServerError(), SCOPE_UNAVAILABLE),
    ],
)
async def test_scope_guard_reports_the_real_cause(error, expected_text):
    guard = TopicGuard(FailingModel(error), "company_assistant", Mock(return_value=None))
    decision = await guard._review(callback_context(), "how many employees are there", "input")
    assert decision.parts[0].text == expected_text
    assert "provider detail" not in decision.parts[0].text


@pytest.mark.parametrize(
    "error,expected_text",
    [(RateLimitError(), RATE_LIMITED), (RuntimeError("provider detail"), PLAN_UNAVAILABLE)],
)
async def test_planner_reports_the_real_cause(error, expected_text):
    planner = RequestPlanner(FailingModel(error), Mock(return_value=None))
    context = callback_context()
    planner.reset(context)
    result = await planner.before_agent(context)
    assert result.parts[0].text == expected_text
    assert context.state["plan_status"] == "failed"


class MonthlyQuotaError(Exception):
    status_code = 429

    def __str__(self):
        return (
            "Cohere_chatException - You are using a Trial key, which is limited to "
            "1000 API calls / month."
        )


class PerMinuteError(Exception):
    status_code = 429

    def __str__(self):
        return "Cohere_chatException - Trial key, limited to 20 API calls / minute."


def test_monthly_cap_is_told_apart_from_a_per_minute_limit():
    assert is_quota_exhausted(MonthlyQuotaError()) is True
    assert is_quota_exhausted(PerMinuteError()) is False
    assert is_quota_exhausted(RuntimeError("monthly report failed")) is False
    assert is_transient(MonthlyQuotaError()) is False
    assert is_transient(PerMinuteError()) is True
    assert provider_failure_message(MonthlyQuotaError()) == QUOTA_EXHAUSTED
    assert provider_failure_message(PerMinuteError()) == RATE_LIMITED
    assert provider_failure_message(RuntimeError("x")) is None
    assert provider_failure_message(None) is None


async def test_monthly_cap_is_not_retried(monkeypatch, sleeps):
    calls = patch_provider(monkeypatch, [[MonthlyQuotaError()], [reply("never reached")]])
    with pytest.raises(MonthlyQuotaError):
        await collect(resilient())
    assert len(calls) == 1
    assert sleeps == []


async def test_every_layer_reports_the_monthly_cap_accurately():
    guard = TopicGuard(
        FailingModel(MonthlyQuotaError()), "company_assistant", Mock(return_value=None)
    )
    decision = await guard._review(callback_context(), "how many employees are there", "input")
    assert decision.parts[0].text == QUOTA_EXHAUSTED
    planner = RequestPlanner(FailingModel(MonthlyQuotaError()), Mock(return_value=None))
    context = callback_context()
    planner.reset(context)
    assert (await planner.before_agent(context)).parts[0].text == QUOTA_EXHAUSTED
    from company_assistant.sub_agents.common import model_error

    response = model_error(context, None, MonthlyQuotaError())
    assert response.content.parts[0].text == QUOTA_EXHAUSTED
    assert "Trial" not in QUOTA_EXHAUSTED
