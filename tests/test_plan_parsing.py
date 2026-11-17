import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from company_assistant.model_resilience import RATE_LIMITED
from company_assistant.planning import (
    PLAN_UNAVAILABLE,
    PlanFormatError,
    RequestPlanner,
    parse_plan,
)

VALID = {
    "steps": [{"agent": "workforce_agent", "task": "Get the current headcount.", "depends_on": []}],
    "clarification": "none",
}
UNKNOWN_AGENT = {
    "steps": [{"agent": "shell_agent", "task": "Run commands", "depends_on": []}],
    "clarification": "none",
}
VALID_TEXT = json.dumps(VALID)


@pytest.mark.parametrize(
    "reply",
    [
        VALID_TEXT,
        f"Example user question: how many people\n{VALID_TEXT}",
        f"```json\n{VALID_TEXT}\n```",
        f"{VALID_TEXT}\nThat is the plan.",
        f"  \n{VALID_TEXT}  ",
    ],
)
def test_a_valid_plan_is_read_even_with_stray_text_around_it(reply):
    plan = parse_plan(reply)
    assert [step.agent for step in plan.steps] == ["workforce_agent"]


@pytest.mark.parametrize(
    "reply,reason",
    [
        ("I think you should ask HR.", "no JSON object in the reply"),
        ('{"steps": [', "not valid JSON"),
        (json.dumps(UNKNOWN_AGENT), "invalid plan (steps.0.agent: literal_error)"),
        (json.dumps({**VALID, "extra": 1}), "extra: extra_forbidden"),
        (json.dumps({"steps": VALID["steps"] * 7, "clarification": "none"}), "steps: too_long"),
        (json.dumps([1, 2]), "no JSON object in the reply"),
    ],
)
def test_the_schema_is_still_strict(reply, reason):
    with pytest.raises(PlanFormatError) as raised:
        parse_plan(reply)
    assert reason in str(raised.value)
    assert "shell_agent" not in str(raised.value)


class SequenceModel:
    model = "cohere_chat/test-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def context():
    return SimpleNamespace(
        agent_name="company_assistant",
        invocation_id="inv",
        user_content=types.Content(role="user", parts=[types.Part(text="how many people")]),
        state={"user_id": "E1001"},
    )


async def plan_with(model, gate=None):
    planner = RequestPlanner(model, gate or Mock(return_value=None))
    ctx = context()
    planner.reset(ctx)
    return await planner.before_agent(ctx), ctx


pytestmark = pytest.mark.integration


async def test_a_malformed_plan_gets_one_repair_attempt_that_can_succeed(caplog):
    model = SequenceModel(UNKNOWN_AGENT, VALID)
    with caplog.at_level(logging.WARNING):
        reply, ctx = await plan_with(model)
    assert reply is None
    assert ctx.state["plan_status"] == "ready"
    assert len(model.requests) == 2
    repair = model.requests[1].contents
    assert len(repair) == 3
    assert repair[1].role == "model" and repair[2].role == "user"
    assert "invalid plan (steps.0.agent: literal_error)" in repair[2].parts[0].text
    assert "detail=invalid plan (steps.0.agent: literal_error)" in caplog.text


async def test_two_malformed_plans_are_blocked_after_exactly_two_calls():
    model = SequenceModel(UNKNOWN_AGENT, "not a plan")
    reply, ctx = await plan_with(model)
    assert reply.parts[0].text == PLAN_UNAVAILABLE
    assert ctx.state["plan_status"] == "failed"
    assert ctx.state["request_plan"] is None
    assert len(model.requests) == 2


async def test_provider_failures_are_not_repaired_or_retried_here(caplog):
    model = SequenceModel(RuntimeError("provider-secret-detail"), VALID)
    with caplog.at_level(logging.WARNING):
        reply, _ = await plan_with(model)
    assert reply.parts[0].text == PLAN_UNAVAILABLE
    assert len(model.requests) == 1
    assert "provider-secret-detail" not in caplog.text


async def test_a_rate_limit_still_gets_its_own_message_without_a_repair():
    class Limited(Exception):
        status_code = 429

    model = SequenceModel(Limited("per minute"), VALID)
    reply, _ = await plan_with(model)
    assert reply.parts[0].text == RATE_LIMITED
    assert len(model.requests) == 1


async def test_a_reply_containing_the_api_key_is_never_repaired(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "sk-test-secret-value")
    leaked = f"{VALID_TEXT} sk-test-secret-value"
    model = SequenceModel(leaked, VALID)
    reply, _ = await plan_with(model)
    assert reply.parts[0].text == PLAN_UNAVAILABLE
    assert len(model.requests) == 1


async def test_the_repair_attempt_respects_the_model_call_budget():
    blocked = LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="Processing limit reached.")])
    )
    gate = Mock(side_effect=[None, blocked])
    model = SequenceModel(UNKNOWN_AGENT, VALID)
    reply, ctx = await plan_with(model, gate)
    assert reply.parts[0].text == "Processing limit reached."
    assert len(model.requests) == 1
    assert ctx.state["plan_status"] == "failed"
