from typing import Any

from google.adk.agents import Agent

from company_assistant.sub_agents.common import make_agent
from company_assistant.tools.client_project_tools import (
    get_client_count,
    get_client_details,
    get_project_details,
    get_project_team,
    list_clients,
    list_projects,
)

TOOLS = [
    get_client_count,
    list_clients,
    get_client_details,
    list_projects,
    get_project_details,
    get_project_team,
]


def create_agent(model: Any) -> Agent:
    return make_agent(
        "client_project_agent",
        "Answers client counts, client lists, projects, project details, and project teams.",
        list(TOOLS),
        model,
    )
