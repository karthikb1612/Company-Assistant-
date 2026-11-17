from dataclasses import dataclass

POLICIES = {
    "get_headcount": "public",
    "get_bench_summary": "public",
    "get_client_count": "public",
    "list_clients": "public",
    "get_client_details": "public",
    "list_projects": "public",
    "get_project_details": "public",
    "get_layoff_count": "privileged",
    "get_attrition_stats": "privileged",
    "list_bench_employees": "privileged",
    "list_new_hires": "privileged",
    "list_employees": "privileged",
    "get_project_team": "privileged",
    "find_employee": "search",
    "get_employee_profile": "employee",
    "get_employee_projects": "employee",
    "get_employee_email": "employee",
    "get_employee_manager": "employee",
    "list_direct_reports": "employee",
    "workforce_agent": "public",
    "client_project_agent": "public",
    "employee_profile_agent": "public",
}
EMPLOYEE_TOOLS = frozenset(name for name, policy in POLICIES.items() if policy == "employee")
AGENT_TOOLS = frozenset(name for name in POLICIES if name.endswith("_agent"))


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str


def can_call(
    actor: Actor | None,
    tool_name: str,
    target_id: str | None = None,
    target_manager: str | None = None,
) -> bool:
    """Authorize a canonical target using trusted database-derived identity."""
    if actor is None or actor.role not in {"employee", "manager", "hr"}:
        return False
    policy = POLICIES.get(tool_name)
    if policy in {"public", "search"}:
        return True
    if policy == "privileged":
        return actor.role in {"manager", "hr"}
    if policy == "employee" and target_id:
        return (
            actor.role == "hr"
            or actor.user_id == target_id
            or (actor.role == "manager" and actor.user_id == target_manager)
        )
    return False
