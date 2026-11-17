from typing import Any

from google.adk.agents import Agent

from company_assistant.sub_agents.common import make_agent
from company_assistant.tools.employee_tools import (
    find_employee,
    get_employee_email,
    get_employee_manager,
    get_employee_profile,
    get_employee_projects,
    list_direct_reports,
)

TOOLS = [
    find_employee,
    get_employee_profile,
    get_employee_projects,
    get_employee_email,
    get_employee_manager,
    list_direct_reports,
]


def create_agent(model: Any) -> Agent:
    return make_agent(
        "employee_profile_agent",
        "Looks up authorized employees by name or ID: profile, email, manager, direct reports, "
        "experience, and project history.",
        list(TOOLS),
        model,
    )
