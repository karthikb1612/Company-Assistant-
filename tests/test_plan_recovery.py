import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from company_assistant.planning import PLAN_INCOMPLETE, RequestPlanner

PLAN = {
    "steps": [
        {"agent": "workforce_agent", "task": "Get the headcount.", "depends_on": []},
        {"agent": "client_project_agent", "task": "Get the client count.", "depends_on": []},
    ],
    "clarification": "none",
}


def reply(text):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def context(**state):
    base = {
        "request_plan": PLAN,
        "plan_invocation_id": "inv",
        "plan_next_step": 0,
        "plan_pending": False,
        "plan_status": "ready",
        "plan_synth_calls": 0,
    }
    return SimpleNamespace(invocation_id="inv", state={**base, **state})


def planner():
    return RequestPlanner(Mock(), Mock(return_value=None))


def text_of(response):
    return response.content.parts[0].text


def test_a_text_reply_before_any_delegation_runs_the_first_planned_step(caplog):
    ctx = context()
    with caplog.at_level(logging.WARNING):
        result = planner().guard_final(ctx, reply("Let me think about that."))
    call = result.content.parts[0].function_call
    assert (call.name, call.args) == ("workforce_agent", {"request": "Get the headcount."})
    assert ctx.state["plan_synth_calls"] == 1
    assert "running planned step 1 of 2" in caplog.text
    assert "Let me think" not in caplog.text


def test_a_later_text_reply_runs_the_next_step_after_one_has_returned():
    ctx = context(plan_next_step=1, plan_status="ready")
    call = planner().guard_final(ctx, reply("Done already.")).content.parts[0].function_call
    assert call.name == "client_project_agent"


def test_recovery_is_bounded_by_the_number_of_planned_steps(caplog):
    ctx = context(plan_synth_calls=2)
    with caplog.at_level(logging.WARNING):
        result = planner().guard_final(ctx, reply("Still not delegating."))
    assert text_of(result) == PLAN_INCOMPLETE
    assert "plan left incomplete" in caplog.text


@pytest.mark.parametrize(
    "state",
    [
        {"plan_pending": True},
        {"plan_invocation_id": "another-turn"},
        {"request_plan": None},
        {"plan_next_step": 2},
    ],
)
def test_no_step_is_invented_when_the_plan_cannot_safely_continue(state):
    result = planner().guard_final(context(**state), reply("An invented answer."))
    assert text_of(result) == PLAN_INCOMPLETE


def test_a_completed_plan_and_real_tool_calls_pass_through_untouched():
    done = context(plan_next_step=2, plan_status="completed")
    assert planner().guard_final(done, reply("Final answer.")) is None
    call = types.Part(function_call=types.FunctionCall(name="workforce_agent", args={}))
    response = LlmResponse(content=types.Content(role="model", parts=[call]))
    assert planner().guard_final(context(), response) is None
    assert planner().guard_final(context(), LlmResponse(partial=True)) is None
