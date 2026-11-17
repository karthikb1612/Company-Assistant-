import pytest

from company_assistant.agent import create_root_agent
from company_assistant.sub_agents.common import SAFETY


def test_every_agent_is_told_never_to_drop_a_stated_constraint():
    assert "Apply every constraint the user stated" in SAFETY
    assert "never present results as if they met a constraint you did not apply" in SAFETY


@pytest.mark.integration
def test_the_agents_instructions_carry_the_rule_and_the_skill_wording():
    from tests.test_agents import ScriptedModel

    root = create_root_agent(ScriptedModel())
    agents = [root, *[tool.agent for tool in root.tools]]
    for agent in agents:
        assert "Apply every constraint the user stated" in agent.instruction
    assert "Honor every constraint in the question" in root.instruction
    workforce = next(tool.agent for tool in root.tools if tool.agent.name == "workforce_agent")
    assert "project_name or client_name" in workforce.instruction
    assert "order_by" in workforce.instruction
