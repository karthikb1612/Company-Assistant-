import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from company_assistant import planning
from company_assistant.planning import (
    PLAN_INSTRUCTION,
    ROUTING_EXAMPLES,
    QuestionPlan,
    RequestPlanner,
    capability_table,
    merge_independent_steps,
    planner_instruction,
    routing_examples,
)
from company_assistant.sub_agents import client_project, employee_profile, workforce

ALL_TOOLS = {
    "workforce_agent": workforce.TOOLS,
    "client_project_agent": client_project.TOOLS,
    "employee_profile_agent": employee_profile.TOOLS,
}


def test_the_table_lists_every_tool_each_specialist_really_has():
    table = capability_table()
    for agent, tools in ALL_TOOLS.items():
        assert f"{agent}:" in table
        for tool in tools:
            assert f"- {tool.__name__}(" in table


def test_the_table_exposes_real_parameters_and_hides_internals():
    table = capability_table()
    assert "include_projects" in table
    assert "name_contains" in table
    assert "tool_context" not in table
    line = next(row for row in table.splitlines() if "- list_employees(" in row)
    assert "current project" in line


def test_a_new_tool_appears_in_the_table_without_editing_the_planner(monkeypatch):
    def list_widgets(color: str | None = None):
        """List widgets of one color."""

    monkeypatch.setattr(workforce, "TOOLS", [*workforce.TOOLS, list_widgets])
    capability_table.cache_clear()
    try:
        assert "- list_widgets(color): List widgets of one color." in capability_table()
    finally:
        capability_table.cache_clear()


def test_agents_are_built_from_the_same_tool_lists():
    from company_assistant.agent import create_root_agent
    from tests.test_agents import ScriptedModel

    root = create_root_agent(ScriptedModel())
    built = {tool.agent.name: [t.__name__ for t in tool.agent.tools] for tool in root.tools}
    for agent, tools in ALL_TOOLS.items():
        assert built[agent] == [t.__name__ for t in tools]


@pytest.mark.parametrize("question,steps", ROUTING_EXAMPLES)
def test_every_example_plan_is_valid_and_survives_merging(question, steps):
    plan = QuestionPlan.model_validate(
        {
            "steps": [{"agent": a, "task": t, "depends_on": d} for a, t, d in steps],
            "clarification": "none",
        }
    )
    assert [s.agent for s in merge_independent_steps(plan).steps] == [s.agent for s in plan.steps]
    for agent, _, _ in steps:
        assert agent in ALL_TOOLS


def test_the_data_engineers_with_projects_example_is_a_single_workforce_step():
    question, steps = next(e for e in ROUTING_EXAMPLES if "project name" in e[0])
    assert question == "give 10 data engineers names with project name"
    assert [agent for agent, _, _ in steps] == ["workforce_agent"]
    assert "include_projects" in steps[0][1] and "limit 10" in steps[0][1]


def test_the_planner_prompt_contains_rules_table_and_examples():
    prompt = planner_instruction()
    assert prompt.startswith(PLAN_INSTRUCTION)
    assert capability_table() in prompt
    assert routing_examples() in prompt
    assert "stays ONE step for ONE specialist" in prompt
    example = next(row for row in routing_examples().splitlines() if row.startswith("{"))
    assert json.loads(example)["clarification"] == "none"


class RequestCapturingModel:
    model = "cohere_chat/test-model"

    def __init__(self):
        self.instructions = []

    async def generate_content_async(self, request, stream=False):
        self.instructions.append(request.config.system_instruction)
        plan = {"steps": [], "clarification": "request"}
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps(plan))])
        )


@pytest.mark.integration
async def test_the_planner_request_carries_the_generated_table():
    model = RequestCapturingModel()
    planner = RequestPlanner(model, Mock(return_value=None))
    context = SimpleNamespace(
        agent_name="company_assistant",
        invocation_id="inv",
        user_content=types.Content(role="user", parts=[types.Part(text="hello")]),
        state={},
    )
    planner.reset(context)
    await planner.before_agent(context)
    assert model.instructions == [planning.planner_instruction()]
    assert "- list_employees(" in model.instructions[0]


def test_the_only_those_on_a_project_example_uses_the_assigned_filter():
    question, steps = next(e for e in ROUTING_EXAMPLES if "who are on a project" in e[0])
    assert [agent for agent, _, _ in steps] == ["workforce_agent"]
    assert "assigned true" in steps[0][1] and "include_projects true" in steps[0][1]
    assert "join dates" in steps[0][1]
    assert "assigned" in capability_table()
