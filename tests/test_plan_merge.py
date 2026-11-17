import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from company_assistant.planning import (
    DELEGATED_REQUEST_LIMIT,
    PLAN_TASK_LIMIT,
    PlanStep,
    QuestionPlan,
    RequestPlanner,
    delegated_request,
    merge_independent_steps,
)

W, C, E = "workforce_agent", "client_project_agent", "employee_profile_agent"


def make_plan(*steps, clarification="none"):
    return QuestionPlan.model_validate(
        {
            "steps": [
                {"agent": agent, "task": task, "depends_on": list(depends)}
                for agent, task, depends in steps
            ],
            "clarification": clarification,
        }
    )


def shape(plan):
    return [(step.agent, step.task, step.depends_on) for step in plan.steps]


def test_independent_steps_for_one_specialist_become_one_delegation():
    plan = make_plan(
        (W, "headcount", ()),
        (W, "people per role", ()),
        (C, "client count", ()),
        (C, "active projects", ()),
        (W, "layoffs in 2025", ()),
    )
    merged = merge_independent_steps(plan)
    assert shape(merged) == [
        (
            W,
            "Answer each of these requests: (1) headcount; (2) people per role; (3) layoffs in 2025.",
            [],
        ),
        (C, "Answer each of these requests: (1) client count; (2) active projects.", []),
    ]


def test_order_of_first_appearance_is_kept_when_specialists_interleave():
    merged = merge_independent_steps(
        make_plan((C, "clients", ()), (W, "bench", ()), (C, "projects", ()))
    )
    assert [step.agent for step in merged.steps] == [C, W]
    assert merged.steps[0].task == "Answer each of these requests: (1) clients; (2) projects."


def test_dependent_steps_are_never_merged_and_dependencies_are_renumbered():
    plan = make_plan(
        (W, "bench names", ()),
        (E, "profile of the first one", (1,)),
        (W, "exits in 2025", ()),
        (E, "history of the same person", (2,)),
    )
    merged = merge_independent_steps(plan)
    assert shape(merged) == [
        (W, "Answer each of these requests: (1) bench names; (2) exits in 2025.", []),
        (E, "profile of the first one", [1]),
        (E, "history of the same person", [2]),
    ]


def test_a_step_that_depends_on_a_merged_step_points_at_the_merged_one():
    plan = make_plan((W, "a", ()), (C, "b", ()), (W, "c", ()), (E, "uses c", (3,)))
    merged = merge_independent_steps(plan)
    assert shape(merged)[-1] == (E, "uses c", [1])


def test_merge_is_skipped_when_the_combined_task_would_be_too_long():
    long_task = "x" * (PLAN_TASK_LIMIT - 20)
    merged = merge_independent_steps(make_plan((W, long_task, ()), (W, long_task, ())))
    assert len(merged.steps) == 2


def test_single_step_empty_and_clarification_plans_are_unchanged():
    one = make_plan((W, "headcount", ()))
    assert shape(merge_independent_steps(one)) == shape(one)
    empty = make_plan()
    assert merge_independent_steps(empty).steps == []
    ask = make_plan(clarification="date_range")
    assert merge_independent_steps(ask).clarification == "date_range"


class FixedPlanModel:
    model = "cohere_chat/test-model"

    def __init__(self, plan):
        self.plan = plan

    async def generate_content_async(self, request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps(self.plan))])
        )


@pytest.mark.integration
async def test_planner_stores_the_merged_plan():
    raw = {
        "steps": [
            {"agent": W, "task": "headcount", "depends_on": []},
            {"agent": W, "task": "layoffs in 2025", "depends_on": []},
            {"agent": C, "task": "client count", "depends_on": []},
            {"agent": C, "task": "projects on hold", "depends_on": []},
        ],
        "clarification": "none",
    }
    planner = RequestPlanner(FixedPlanModel(raw), Mock(return_value=None))
    context = SimpleNamespace(
        agent_name="company_assistant",
        invocation_id="inv",
        user_content=types.Content(role="user", parts=[types.Part(text="board snapshot")]),
        state={},
    )
    planner.reset(context)
    assert await planner.before_agent(context) is None
    stored = context.state["request_plan"]["steps"]
    assert [step["agent"] for step in stored] == [W, C]
    assert stored[0]["task"] == "Answer each of these requests: (1) headcount; (2) layoffs in 2025."
    assert context.state["plan_status"] == "ready"


def step(task, depends_on=()):
    return PlanStep(agent=C, task=task, depends_on=list(depends_on))


def test_a_specialist_receives_the_plan_task_not_the_models_rewording():
    task = "Show the status and projects of client Juniper"
    assert delegated_request(step(task), "Juniper") == task
    assert delegated_request(step(task), None) == task
    assert delegated_request(step(task), "   ") == task
    assert delegated_request(step(task), 7) == task


def test_dependent_steps_keep_the_models_context_after_the_plan_task():
    task = "Get the profile of the first bench employee"
    combined = delegated_request(step(task, (1,)), "first bench employee is E1051")
    assert combined.startswith(task)
    assert "Context from earlier steps: first bench employee is E1051" in combined
    assert delegated_request(step(task, (1,)), None) == task
    already = f"{task} (E1051)"
    assert delegated_request(step(task, (1,)), already) == already
    assert len(delegated_request(step(task, (1,)), "x" * 9000)) == DELEGATED_REQUEST_LIMIT


def test_before_tool_replaces_the_request_with_the_plan_task():
    planner = RequestPlanner(Mock(), Mock(return_value=None))
    context = SimpleNamespace(
        invocation_id="inv",
        function_call_id="call-1",
        state={
            "request_plan": {
                "steps": [
                    {
                        "agent": C,
                        "task": "Show the status and projects of client Juniper",
                        "depends_on": [],
                    }
                ],
                "clarification": "none",
            },
            "plan_invocation_id": "inv",
            "plan_next_step": 0,
            "plan_pending": False,
        },
    )
    args = {"request": "Juniper"}
    assert planner.before_tool(SimpleNamespace(name=C), args, context) is None
    assert args == {"request": "Show the status and projects of client Juniper"}
    rejected = {"request": "something else"}
    denial = planner.before_tool(SimpleNamespace(name=C), rejected, context)
    assert denial["code"] == "plan_mismatch"
    assert rejected == {"request": "something else"}


def test_merged_requests_are_a_numbered_list_a_specialist_can_follow():
    from company_assistant.planning import numbered_requests

    assert numbered_requests(["a.", "b"]) == "Answer each of these requests: (1) a; (2) b."
    plan = make_plan(*[(W, f"question {n}", ()) for n in range(1, 6)])
    merged = merge_independent_steps(plan)
    assert len(merged.steps) == 1
    task = merged.steps[0].task
    assert task.startswith("Answer each of these requests: (1) question 1;")
    assert "(5) question 5" in task and len(task) <= PLAN_TASK_LIMIT
    assert "also" not in task
