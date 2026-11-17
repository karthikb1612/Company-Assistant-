from typing import Any

from company_assistant.tools import common
from company_assistant.tools.common import (
    PROJECT_STATUSES,
    failure,
    iso_date,
    like_pattern,
    query,
    safe_tool,
    success,
    text_filter,
)

PROJECT_COLUMNS = "projects.project_id, projects.name, clients.name AS client_name, projects.status, projects.start_date, projects.end_date"


@safe_tool
def get_client_count() -> dict[str, Any]:
    """Get total clients including active and inactive counts."""
    rows = query("SELECT status, COUNT(*) AS count FROM clients GROUP BY status")
    counts = {row["status"]: row["count"] for row in rows}
    return success(
        {
            "total": sum(counts.values()),
            "active": counts.get("active", 0),
            "inactive": counts.get("inactive", 0),
        }
    )


@safe_tool
def list_clients(status: str | None = None, industry: str | None = None) -> dict[str, Any]:
    """List clients with industry, status, and start date, optionally filtered.

    Args:
        status: Optional client status, active or inactive.
        industry: Optional case-insensitive industry, for example Technology.
    """
    selected_status = text_filter(status).lower() if status is not None else None
    if selected_status not in {None, "active", "inactive"}:
        raise ValueError("Client status must be active or inactive.")
    selected_industry = text_filter(industry) if industry is not None else None
    rows = query(
        "SELECT client_id, name, industry, status, start_date FROM clients "
        "WHERE (? IS NULL OR status = ?) AND (? IS NULL OR industry = ? COLLATE NOCASE) "
        "ORDER BY name",
        (selected_status, selected_status, selected_industry, selected_industry),
    )
    filters = {
        key: value
        for key, value in (("status", selected_status), ("industry", selected_industry))
        if value is not None
    }
    return success(rows, filters=filters) if filters else success(rows)


@safe_tool
def get_client_details(client_name: str) -> dict[str, Any]:
    """Get one client's industry, status, start date, and project counts by status.

    Use it for "tell me about <client>"; use list_projects for the project list itself.

    Args:
        client_name: Case-insensitive exact client name, for example Juniper.
    """
    selected_name = text_filter(client_name)
    rows = query(
        "SELECT client_id, name, industry, status, start_date FROM clients "
        "WHERE name = ? COLLATE NOCASE",
        (selected_name,),
    )
    if not rows:
        return failure("not_found", "Client not found.")
    client = rows[0]
    counts = {
        row["status"]: row["count"]
        for row in query(
            "SELECT status, COUNT(*) AS count FROM projects WHERE client_id = ? GROUP BY status",
            (client.pop("client_id"),),
        )
    }
    client["projects"] = {
        "total": sum(counts.values()),
        "active": counts.get("active", 0),
        "on_hold": counts.get("on_hold", 0),
        "completed": counts.get("completed", 0),
    }
    return success(client, filters={"client_name": selected_name})


@safe_tool
def list_projects(
    client_name: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    name_contains: str | None = None,
) -> dict[str, Any]:
    """List projects with optional client, status, name, and active-window filters.
    When start_date and end_date are provided, only projects whose timeline overlaps the window are returned.

    Args:
        client_name: Case-insensitive exact client name, for example Acme.
        status: active, completed, or on_hold; omit for all statuses.
        start_date: Window start in YYYY-MM-DD or MM-DD-YY; use with end_date to filter overlapping projects.
        end_date: Window end in YYYY-MM-DD or MM-DD-YY; use with start_date to filter overlapping projects.
        name_contains: Optional case-insensitive part of a project name, for example Project 07.
    """
    selected_name = text_filter(name_contains) if name_contains is not None else None
    selected_client = text_filter(client_name) if client_name is not None else None
    selected_status = text_filter(status).lower() if status is not None else None
    if selected_status is not None and selected_status not in PROJECT_STATUSES:
        raise ValueError("Project status must be active, completed, or on_hold.")
    win_start = iso_date(start_date) if start_date is not None else None
    win_end = iso_date(end_date) if end_date is not None else None
    if (win_start is None) != (win_end is None):
        raise ValueError("Provide both start_date and end_date, or neither.")
    if win_start and win_end and win_start > win_end:
        raise ValueError("start_date must be on or before end_date.")
    if selected_client and not query(
        "SELECT client_id FROM clients WHERE name = ? COLLATE NOCASE", (selected_client,)
    ):
        return failure("not_found", "Client not found.")
    rows = query(
        f"SELECT {PROJECT_COLUMNS} FROM projects JOIN clients USING (client_id) "
        "WHERE (? IS NULL OR clients.name = ? COLLATE NOCASE) "
        "AND (? IS NULL OR projects.status = ?) "
        "AND (? IS NULL OR projects.start_date <= ?) "
        "AND (? IS NULL OR projects.end_date IS NULL OR projects.end_date >= ?) "
        "AND (? IS NULL OR projects.name LIKE ? ESCAPE '\\') "
        "ORDER BY clients.name, projects.name",
        (
            selected_client,
            selected_client,
            selected_status,
            selected_status,
            win_end,
            win_end,
            win_start,
            win_start,
            selected_name,
            like_pattern(selected_name) if selected_name else None,
        ),
    )
    filters = {
        "client_name": selected_client,
        "status": selected_status,
        "start_date": win_start,
        "end_date": win_end,
    }
    if selected_name is not None:
        filters["name_contains"] = selected_name
    return success(rows, filters=filters)


@safe_tool
def get_project_details(project_name: str) -> dict[str, Any]:
    """Get a project's client, dates, status, and current team size without a roster.

    Args:
        project_name: Case-insensitive exact project name from list_projects.
    """
    selected_name = text_filter(project_name)
    as_of = common.today().isoformat()
    rows = query(
        f"SELECT {PROJECT_COLUMNS}, (SELECT COUNT(DISTINCT assignments.emp_id) FROM assignments "
        "JOIN employees ON employees.emp_id = assignments.emp_id "
        "WHERE assignments.project_id = projects.project_id AND employees.status IN ('active', 'bench') "
        "AND assignments.start_date <= ? AND (assignments.end_date IS NULL OR assignments.end_date >= ?)) AS team_size "
        "FROM projects JOIN clients USING (client_id) WHERE projects.name = ? COLLATE NOCASE",
        (as_of, as_of, selected_name),
    )
    return success(rows[0]) if rows else failure("not_found", "Project not found.")


@safe_tool
def get_project_team(project_name: str) -> dict[str, Any]:
    """List the current team of a project (employees assigned today); HR and managers only.

    Args:
        project_name: Case-insensitive exact project name from list_projects.
    """
    selected_name = text_filter(project_name)
    projects = query(
        "SELECT project_id, name FROM projects WHERE name = ? COLLATE NOCASE", (selected_name,)
    )
    if not projects:
        return failure("not_found", "Project not found.")
    as_of = common.today().isoformat()
    team = query(
        "SELECT employees.emp_id, employees.name, assignments.role_on_project, "
        "assignments.start_date FROM assignments "
        "JOIN employees ON employees.emp_id = assignments.emp_id "
        "WHERE assignments.project_id = ? AND employees.status IN ('active', 'bench') "
        "AND assignments.start_date <= ? "
        "AND (assignments.end_date IS NULL OR assignments.end_date >= ?) "
        "ORDER BY employees.name, employees.emp_id",
        (projects[0]["project_id"], as_of, as_of),
    )
    return success(
        team,
        project=projects[0]["name"],
        team_size=len({member["emp_id"] for member in team}),
        filters={"project_name": selected_name},
    )
