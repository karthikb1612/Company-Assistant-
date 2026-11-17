from pathlib import Path

from google.adk.skills import Skill, load_skill_from_dir

AGENT_SKILLS = {
    "company_assistant": "company-assistant",
    "workforce_agent": "workforce",
    "client_project_agent": "client-project",
    "employee_profile_agent": "employee-profile",
}


def load_agent_skill(agent_name: str) -> Skill:
    """Load only the trusted, locally assigned skill through ADK's validator."""
    if agent_name not in AGENT_SKILLS:
        raise ValueError("Unknown agent skill.")
    return load_skill_from_dir(Path(__file__).resolve().parent / AGENT_SKILLS[agent_name])
