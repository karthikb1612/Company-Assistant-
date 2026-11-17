from datetime import date
from typing import Any

from google.adk.tools import ToolContext

from company_assistant.config import get_settings
from company_assistant.security.callbacks import resolve_actor
from company_assistant.tools import common
from company_assistant.tools.common import (
    employee_id,
    failure,
    like_pattern,
    list_summary,
    query,
    row_limit,
    safe_tool,
    success,
    text_filter,
)

MAX_MATCHES = 20


def remember_target(tool_context: ToolContext, target: str) -> None:
    tool_context.state["last_emp_id"] = target
    tool_context.state[f"lookup_target:{target}"] = True


@safe_tool
def find_employee(tool_context: ToolContext, name: str) -> dict[str, Any]:
    """Find employees you may access by full or partial name and return their IDs.

    Use this first whenever a person is named instead of given by ID.

    Args:
        name: Full or partial employee name, for example Henry Park or park.
    """
    text = text_filter(name)
    actor = resolve_actor(get_settings())
    tool_context.state["last_emp_id"] = None
    if actor is None:
        return failure(
            "permission_denied", "You do not have permission to access that information."
        )
    tokens = text.split()[:5]
    clauses = " AND ".join("name LIKE ? ESCAPE '\\'" for _ in tokens)
    parameters: list[str] = [like_pattern(token) for token in tokens]
    if actor.role == "manager":
        clauses += " AND (emp_id = ? OR manager_id = ?)"
        parameters += [actor.user_id, actor.user_id]
    elif actor.role != "hr":
        clauses += " AND emp_id = ?"
        parameters.append(actor.user_id)
    total = query(f"SELECT COUNT(*) AS count FROM employees WHERE {clauses}", tuple(parameters))[0][
        "count"
    ]
    rows = query(
        "SELECT emp_id, name, role, department, status FROM employees "
        f"WHERE {clauses} ORDER BY name, emp_id LIMIT {MAX_MATCHES}",
        tuple(parameters),
    )
    if total == 1:
        remember_target(tool_context, rows[0]["emp_id"])
    return success(rows, total_matches=total, filters={"name": text})


def resolve_target(tool_context: ToolContext, emp_id: str | None) -> str:
    target = emp_id if emp_id is not None else tool_context.state.get("last_emp_id")
    tool_context.state["last_emp_id"] = None
    if target is None:
        raise LookupError("Which employee ID should I look up?")
    return employee_id(target)


@safe_tool
def get_employee_profile(tool_context: ToolContext, emp_id: str | None = None) -> dict[str, Any]:
    """Get an authorized employee's profile, approximate experience, and distinct project count.

    Args:
        emp_id: Employee ID such as E1023; omit only for an unambiguous session follow-up.
    """
    target = resolve_target(tool_context, emp_id)
    rows = query(
        "SELECT emp_id, name, role, department, status, join_date, exit_date, prior_experience_years, "
        "(SELECT COUNT(DISTINCT project_id) FROM assignments WHERE emp_id = employees.emp_id) AS project_count "
        "FROM employees WHERE emp_id = ?",
        (target,),
    )
    if not rows:
        return failure("not_found", "Employee not found.")
    profile = rows[0]
    end_date = date.fromisoformat(profile["exit_date"]) if profile["exit_date"] else common.today()
    years = (end_date - date.fromisoformat(profile["join_date"])).days / 365.2425
    profile["years_at_company"] = round(years, 2)
    profile["total_experience_years"] = round(profile.pop("prior_experience_years") + years, 2)
    tool_context.state["last_emp_id"] = target
    tool_context.state[f"lookup_target:{target}"] = True
    return success(profile)


@safe_tool
def get_employee_projects(tool_context: ToolContext, emp_id: str | None = None) -> dict[str, Any]:
    """List an authorized employee's distinct projects, clients, and assignment periods.

    Args:
        emp_id: Employee ID such as E1023; omit only for an unambiguous session follow-up.
    """
    target = resolve_target(tool_context, emp_id)
    if not query("SELECT emp_id FROM employees WHERE emp_id = ?", (target,)):
        return failure("not_found", "Employee not found.")
    rows = query(
        "SELECT projects.project_id, projects.name, clients.name AS client_name, projects.status, "
        "assignments.start_date, assignments.end_date, assignments.role_on_project "
        "FROM assignments JOIN projects USING (project_id) JOIN clients USING (client_id) "
        "WHERE assignments.emp_id = ? ORDER BY projects.name, assignments.start_date",
        (target,),
    )
    projects: dict[int, dict[str, Any]] = {}
    for row in rows:
        project = projects.setdefault(
            row["project_id"],
            {key: row[key] for key in ("project_id", "name", "client_name", "status")},
        )
        project.setdefault("assignments", []).append(
            {key: row[key] for key in ("start_date", "end_date", "role_on_project")}
        )
    tool_context.state["last_emp_id"] = target
    tool_context.state[f"lookup_target:{target}"] = True
    return success(list(projects.values()), filters={"emp_id": target})


@safe_tool
def get_employee_email(tool_context: ToolContext, emp_id: str | None = None) -> dict[str, Any]:
    """Get an authorized employee's email address.

    Args:
        emp_id: Employee ID such as E1023; omit only for an unambiguous session follow-up.
    """
    target = resolve_target(tool_context, emp_id)
    rows = query("SELECT emp_id, name, email FROM employees WHERE emp_id = ?", (target,))
    if not rows:
        return failure("not_found", "Employee not found.")
    remember_target(tool_context, target)
    return success(rows[0])


@safe_tool
def get_employee_manager(tool_context: ToolContext, emp_id: str | None = None) -> dict[str, Any]:
    """Get the manager an authorized employee reports to.

    Args:
        emp_id: Employee ID such as E1023; omit only for an unambiguous session follow-up.
    """
    target = resolve_target(tool_context, emp_id)
    rows = query(
        "SELECT employee.emp_id, employee.name, manager.emp_id AS manager_id, "
        "manager.name AS manager_name FROM employees AS employee "
        "LEFT JOIN employees AS manager ON manager.emp_id = employee.manager_id "
        "WHERE employee.emp_id = ?",
        (target,),
    )
    if not rows:
        return failure("not_found", "Employee not found.")
    remember_target(tool_context, target)
    return success(rows[0])


@safe_tool
def list_direct_reports(
    tool_context: ToolContext, emp_id: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    """List the current employees (active or bench) who report directly to a manager.

    Args:
        emp_id: ID of the manager whose direct reports to list; omit only for a session follow-up.
        limit: Rows to show, 1 to 100 (default 20). Raise it only when the user asks to see more;
            the total count is always returned.
    """
    target = resolve_target(tool_context, emp_id)
    row_count = row_limit(limit)
    if not query("SELECT emp_id FROM employees WHERE emp_id = ?", (target,)):
        return failure("not_found", "Employee not found.")
    where = "manager_id = ? AND status IN ('active', 'bench')"
    total = query(f"SELECT COUNT(*) AS count FROM employees WHERE {where}", (target,))[0]["count"]
    rows = query(
        "SELECT emp_id, name, role, status FROM employees "
        f"WHERE {where} ORDER BY name, emp_id LIMIT {row_count}",
        (target,),
    )
    remember_target(tool_context, target)
    return success(
        rows,
        total=total,
        truncated=total > len(rows),
        summary=list_summary(total, len(rows)),
        filters={"manager_id": target},
    )
