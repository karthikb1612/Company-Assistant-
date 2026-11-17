import os
from typing import Any

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.tools.agent_tool import AgentTool

from company_assistant.config import get_settings, load_environment
from company_assistant.model_resilience import build_model
from company_assistant.planning import RequestPlanner
from company_assistant.sub_agents import client_project, employee_profile, workforce
from company_assistant.sub_agents.common import make_agent, model_guard


def finalize_employee_context(callback_context: Any) -> None:
    state = callback_context.state.to_dict()
    targets = [key for key, value in state.items() if key.startswith("lookup_target:") and value]
    if len(targets) > 1 or state.get("lookup_failed"):
        callback_context.state["last_emp_id"] = None
    for key in targets:
        callback_context.state[key] = False
    callback_context.state["lookup_failed"] = False


def create_root_agent(model: BaseLlm | None = None) -> Agent:
    """Build fresh specialists; an injected model supports hermetic runner tests."""
    if model is None:
        load_environment()
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        model = build_model(get_settings().model, timeout=30)
    specialists = [
        workforce.create_agent(model),
        client_project.create_agent(model),
        employee_profile.create_agent(model),
    ]
    root = make_agent(
        "company_assistant",
        "Read-only assistant for synthetic workforce, client, project, and employee data.",
        [AgentTool(agent=specialist, skip_summarization=False) for specialist in specialists],
        model,
    )
    planner = RequestPlanner(model, model_guard)
    root.before_agent_callback = [planner.reset, *root.before_agent_callback, planner.before_agent]
    root.before_model_callback = [model_guard, planner.before_model]
    root.before_tool_callback = [root.before_tool_callback, planner.before_tool]
    root.after_tool_callback = planner.after_tool
    root.after_model_callback = [planner.guard_final, root.after_model_callback]
    root.after_agent_callback = finalize_employee_context
    return root


root_agent = create_root_agent()
