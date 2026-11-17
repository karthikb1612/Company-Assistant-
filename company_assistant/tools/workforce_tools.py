from datetime import date
from typing import Any

from company_assistant.tools import common
from company_assistant.tools.common import (
    EXIT_STATUSES,
    effective_year,
    employee_status_filter,
    failure,
    iso_date,
    list_summary,
    query,
    role_filter,
    row_limit,
    safe_tool,
    success,
    text_filter,
)

EMPLOYED = "status IN ('active', 'bench')"


def optional_text(value: str | None) -> str | None:
    return text_filter(value) if value is not None else None


def optional_role(value: str | None) -> str | None:
    return role_filter(value) if value is not None else None


def employee_conditions(role: str | None, department: str | None) -> tuple[list[str], list[Any]]:
    conditions: list[str] = []
    parameters: list[Any] = []
    if role is not None:
        conditions.append("role = ? COLLATE NOCASE")
        parameters.append(role)
    if department is not None:
        conditions.append("department = ? COLLATE NOCASE")
        parameters.append(department)
    return conditions, parameters


def with_filters(filters: dict[str, Any], **optional: Any) -> dict[str, Any]:
    return {**filters, **{key: value for key, value in optional.items() if value is not None}}


@safe_tool
def get_headcount(
    role: str | None = None, department: str | None = None, group_by: str | None = None
) -> dict[str, Any]:
    """Get current employed headcount, including employees on the bench.

    Args:
        role: Optional role: SDE, SDET, QA, DevOps, or Data Engineer.
        department: Optional case-insensitive department filter, for example Engineering.
        group_by: Optional breakdown: role or department.
    """
    selected_role, selected_department = optional_role(role), optional_text(department)
    grouping = text_filter(group_by).lower() if group_by is not None else None
    if grouping not in {None, "role", "department"}:
        raise ValueError("group_by must be role or department.")
    conditions, parameters = employee_conditions(selected_role, selected_department)
    where = " AND ".join([EMPLOYED, *conditions])
    rows = query(
        f"SELECT status, COUNT(*) AS count FROM employees WHERE {where} GROUP BY status",
        tuple(parameters),
    )
    counts = {row["status"]: row["count"] for row in rows}
    active, bench = counts.get("active", 0), counts.get("bench", 0)
    data: dict[str, Any] = {"total": active + bench, "active": active, "bench": bench}
    if grouping:
        grouped = query(
            f"SELECT {grouping} AS label, COUNT(*) AS count FROM employees WHERE {where} "
            f"GROUP BY {grouping} ORDER BY {grouping}",
            tuple(parameters),
        )
        data["breakdown"] = {row["label"]: row["count"] for row in grouped}
    filters = with_filters(
        {}, role=selected_role, department=selected_department, group_by=grouping
    )
    return success(data, filters=filters) if filters else success(data)


def window_bounds(
    year: int | None, start_date: str | None, end_date: str | None
) -> tuple[str, str, dict[str, Any]]:
    if (start_date is None) != (end_date is None):
        raise ValueError("Provide both start_date and end_date, or neither.")
    if start_date is not None and end_date is not None:
        start, end = iso_date(start_date), iso_date(end_date)
        if start > end:
            raise ValueError("start_date must be on or before end_date.")
        return start, end, {"start_date": start, "end_date": end}
    selected_year = effective_year(year)
    return f"{selected_year:04d}-01-01", f"{selected_year:04d}-12-31", {"year": selected_year}


def exit_counts_between(
    start: str, end: str, role: str | None = None, department: str | None = None
) -> dict[str, int]:
    conditions, parameters = employee_conditions(role, department)
    where = " AND ".join(["exit_date >= ?", "exit_date <= ?", *conditions])
    rows = query(
        f"SELECT status, COUNT(*) AS count FROM employees WHERE {where} GROUP BY status",
        (start, end, *parameters),
    )
    counts = dict.fromkeys(EXIT_STATUSES, 0)
    counts.update({row["status"]: row["count"] for row in rows})
    return counts


@safe_tool
def get_layoff_count(
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    role: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    """Count laid-off employees by exit year or date window; HR and managers only.

    Args:
        year: Calendar year; omission selects the current UTC year. Ignored when start_date and end_date are provided.
        start_date: Window start in YYYY-MM-DD or MM-DD-YY; use with end_date for a sub-year range.
        end_date: Window end in YYYY-MM-DD or MM-DD-YY; use with start_date for a sub-year range.
        role: Optional role: SDE, SDET, QA, DevOps, or Data Engineer.
        department: Optional case-insensitive department filter.
    """
    selected_role, selected_department = optional_role(role), optional_text(department)
    start, end, window = window_bounds(year, start_date, end_date)
    count = exit_counts_between(start, end, selected_role, selected_department)["laid_off"]
    filters = with_filters(window, role=selected_role, department=selected_department)
    filters.setdefault("role", None)
    return success({"count": count}, filters=filters)


@safe_tool
def get_attrition_stats(
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    role: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    """Count exits by reason within a year or date window; HR and managers only. This is not a rate.

    Args:
        year: Calendar year; omission selects the current UTC year. Ignored when start_date and end_date are provided.
        start_date: Window start in YYYY-MM-DD or MM-DD-YY; use with end_date for a sub-year range.
        end_date: Window end in YYYY-MM-DD or MM-DD-YY; use with start_date for a sub-year range.
        role: Optional role: SDE, SDET, QA, DevOps, or Data Engineer.
        department: Optional case-insensitive department filter.
    """
    selected_role, selected_department = optional_role(role), optional_text(department)
    start, end, window = window_bounds(year, start_date, end_date)
    counts = exit_counts_between(start, end, selected_role, selected_department)
    filters = with_filters(window, role=selected_role, department=selected_department)
    return success({"total": sum(counts.values()), "breakdown": counts}, filters=filters)


@safe_tool
def get_bench_summary() -> dict[str, Any]:
    """Get current bench count grouped by role, without names or employee IDs."""
    rows = query(
        "SELECT role, COUNT(*) AS count FROM employees WHERE status = 'bench' GROUP BY role ORDER BY role"
    )
    counts = {row["role"]: row["count"] for row in rows}
    return success({"total": sum(counts.values()), "by_role": counts})


@safe_tool
def list_bench_employees(role: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """List bench names, IDs, and bench duration, longest on the bench first; HR and managers only.

    Args:
        role: Optional role: SDE, SDET, QA, DevOps, or Data Engineer.
        limit: Rows to show, 1 to 100 (default 20). Raise it only when the user asks to see more;
            the total count is always returned.
    """
    selected_role = optional_role(role)
    row_count = row_limit(limit)
    where = "status = 'bench' AND (? IS NULL OR role = ? COLLATE NOCASE)"
    parameters = (selected_role, selected_role)
    total = query(f"SELECT COUNT(*) AS count FROM employees WHERE {where}", parameters)[0]["count"]
    rows = query(
        "SELECT emp_id, name, role, bench_since FROM employees "
        f"WHERE {where} ORDER BY bench_since, emp_id LIMIT {row_count}",
        parameters,
    )
    as_of = common.today()
    for row in rows:
        row["days_on_bench"] = (
            (as_of - date.fromisoformat(row["bench_since"])).days if row["bench_since"] else None
        )
    return success(
        rows,
        total=total,
        truncated=total > len(rows),
        summary=list_summary(total, len(rows)),
        filters={"role": selected_role},
    )


@safe_tool
def list_new_hires(
    start_date: str,
    end_date: str,
    role: str | None = None,
    department: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List employees who joined within a date window; HR and managers only.

    Args:
        start_date: Window start in YYYY-MM-DD or MM-DD-YY format.
        end_date: Window end in YYYY-MM-DD or MM-DD-YY format.
        role: Optional role: SDE, SDET, QA, DevOps, or Data Engineer.
        department: Optional case-insensitive department filter.
        limit: Rows to show, 1 to 100 (default 20). Raise it only when the user asks to see more;
            the total count is always returned.
    """
    s, e = iso_date(start_date), iso_date(end_date)
    row_count = row_limit(limit)
    if s > e:
        raise ValueError("start_date must be on or before end_date.")
    selected_role, selected_department = optional_role(role), optional_text(department)
    conditions, parameters = employee_conditions(selected_role, selected_department)
    where = " AND ".join(["join_date >= ?", "join_date <= ?", *conditions])
    values = (s, e, *parameters)
    total = query(f"SELECT COUNT(*) AS count FROM employees WHERE {where}", values)[0]["count"]
    rows = query(
        "SELECT emp_id, name, role, department, status, join_date FROM employees "
        f"WHERE {where} ORDER BY join_date, emp_id LIMIT {row_count}",
        values,
    )
    filters = {"start_date": s, "end_date": e, "role": selected_role}
    return success(
        rows,
        total=total,
        truncated=total > len(rows),
        summary=list_summary(total, len(rows)),
        filters=with_filters(filters, department=selected_department),
    )


CURRENT_ASSIGNMENT = (
    "assignments.start_date <= ? AND (assignments.end_date IS NULL OR assignments.end_date >= ?)"
)
ORDER_COLUMNS = ("name", "join_date", "exit_date")


def assignment_condition(present: bool) -> str:
    """SQL for "has a current project" (present) or "is an active employee with none"."""
    current = (
        "EXISTS (SELECT 1 FROM assignments WHERE assignments.emp_id = employees.emp_id "
        f"AND {CURRENT_ASSIGNMENT})"
    )
    return current if present else f"status = 'active' AND NOT {current}"


def on_named_work_condition(kind: str) -> str:
    """SQL for "currently on this project" or "currently on any project of this client"."""
    clients_join = "JOIN clients USING (client_id) " if kind == "client" else ""
    name_column = "clients.name" if kind == "client" else "projects.name"
    return (
        "EXISTS (SELECT 1 FROM assignments JOIN projects USING (project_id) "
        f"{clients_join}WHERE assignments.emp_id = employees.emp_id "
        f"AND {name_column} = ? COLLATE NOCASE AND {CURRENT_ASSIGNMENT})"
    )


def attach_current_projects(rows: list[dict[str, Any]]) -> None:
    """Add each listed person's current project names with one query, not one lookup per person."""
    if not rows:
        return
    as_of = common.today().isoformat()
    marks = ", ".join("?" for _ in rows)
    assigned = query(
        "SELECT assignments.emp_id AS emp_id, projects.name AS project FROM assignments "
        "JOIN projects USING (project_id) "
        f"WHERE assignments.emp_id IN ({marks}) AND assignments.start_date <= ? "
        "AND (assignments.end_date IS NULL OR assignments.end_date >= ?) "
        "ORDER BY projects.name",
        (*[row["emp_id"] for row in rows], as_of, as_of),
    )
    by_employee: dict[str, list[str]] = {}
    for item in assigned:
        by_employee.setdefault(item["emp_id"], []).append(item["project"])
    for row in rows:
        row["current_projects"] = by_employee.get(row["emp_id"], [])


@safe_tool
def list_employees(
    role: str | None = None,
    department: str | None = None,
    employee_status: str | None = None,
    joined_start: str | None = None,
    joined_end: str | None = None,
    exited_start: str | None = None,
    exited_end: str | None = None,
    unassigned: bool = False,
    limit: int | None = None,
    include_departed: bool = False,
    include_projects: bool = False,
    assigned: bool | None = None,
    project_name: str | None = None,
    client_name: str | None = None,
    order_by: str | None = None,
    descending: bool = False,
) -> dict[str, Any]:
    """List employees matching filters, optionally with each current project name; HR and managers only.

    Never includes exit reasons.

    Use it for lists such as "all QA engineers", "who resigned this year", or "active employees
    with no project". Each window bound is optional and inclusive. Unless employee_status or an
    exit window is given, or include_departed is true, only current employees (active or bench)
    are listed, so "all QA engineers" means the current QA staff.

    Args:
        role: Optional role: SDE, SDET, QA, DevOps, or Data Engineer; 'QA engineer' also works.
        department: Optional case-insensitive department.
        employee_status: active, bench, resigned, laid_off, terminated, or retired.
        joined_start: Earliest join date in YYYY-MM-DD or MM-DD-YY.
        joined_end: Latest join date in YYYY-MM-DD or MM-DD-YY.
        exited_start: Earliest exit date in YYYY-MM-DD or MM-DD-YY.
        exited_end: Latest exit date in YYYY-MM-DD or MM-DD-YY.
        unassigned: True for active employees with no current project assignment.
        assigned: True to list only people who currently have a project assignment ("only those
            on a project"); False is the same as unassigned. Do not combine with unassigned.
        limit: Rows to show, 1 to 100 (default 20). Raise it only when the user asks to see more;
            the total count is always returned.
        include_departed: True to also list people who have left the company.
        include_projects: True to add each listed person's current project names
            (current_projects). Use it for "names with their projects" in a single call;
            past project history is not included.
        project_name: Only people currently on this exact project (case-insensitive).
        client_name: Only people currently on any project of this exact client.
        order_by: name (default), join_date, or exit_date. "Longest serving" is join_date
            ascending; "newest joiners" is join_date descending; "most recently left" is
            exit_date descending.
        descending: True to reverse the order_by direction.
    Every stated constraint must be applied; if none of these filters can express one, say so.
    """
    selected_role, selected_department = optional_role(role), optional_text(department)
    selected_status = employee_status_filter(employee_status) if employee_status else None
    dates = {
        key: iso_date(value)
        for key, value in (
            ("joined_start", joined_start),
            ("joined_end", joined_end),
            ("exited_start", exited_start),
            ("exited_end", exited_end),
        )
        if value is not None
    }
    for first, last in (("joined_start", "joined_end"), ("exited_start", "exited_end")):
        if first in dates and last in dates and dates[first] > dates[last]:
            raise ValueError(f"{first} must be on or before {last}.")
    for flag_name, flag in (
        ("unassigned", unassigned),
        ("include_departed", include_departed),
        ("include_projects", include_projects),
        ("assigned", assigned),
    ):
        if flag not in (True, False, None):
            raise ValueError(f"{flag_name} must be true or false.")
    if unassigned is True and assigned is True:
        raise ValueError("Use either assigned or unassigned, not both.")
    if descending not in (True, False, None):
        raise ValueError("descending must be true or false.")
    selected_order = None
    if order_by is not None:
        selected_order = text_filter(order_by).lower().replace(" ", "_")
        if selected_order not in ORDER_COLUMNS:
            raise ValueError("order_by must be name, join_date, or exit_date.")
    selected_project = optional_text(project_name)
    selected_client = optional_text(client_name)
    if selected_project and not query(
        "SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE", (selected_project,)
    ):
        return failure("not_found", "Project not found.")
    if selected_client and not query(
        "SELECT 1 FROM clients WHERE name = ? COLLATE NOCASE", (selected_client,)
    ):
        return failure("not_found", "Client not found.")
    row_count = row_limit(limit)
    conditions, parameters = employee_conditions(selected_role, selected_department)
    exit_view = selected_status in EXIT_STATUSES or "exited_start" in dates or "exited_end" in dates
    current_only = selected_status is None and not exit_view and include_departed is not True
    if selected_status:
        conditions.append("status = ?")
        parameters.append(selected_status)
    elif current_only:
        conditions.append(EMPLOYED)
    for key, column, operator in (
        ("joined_start", "join_date", ">="),
        ("joined_end", "join_date", "<="),
        ("exited_start", "exit_date", ">="),
        ("exited_end", "exit_date", "<="),
    ):
        if key in dates:
            conditions.append(f"{column} {operator} ?")
            parameters.append(dates[key])
    wants_unassigned = unassigned is True or assigned is False
    if wants_unassigned or assigned is True:
        as_of = common.today().isoformat()
        conditions.append(assignment_condition(present=assigned is True))
        parameters += [as_of, as_of]
    for value, kind in ((selected_project, "project"), (selected_client, "client")):
        if value:
            conditions.append(on_named_work_condition(kind))
            parameters += [value, common.today().isoformat(), common.today().isoformat()]
    where = " AND ".join(conditions) if conditions else "1 = 1"
    total = query(f"SELECT COUNT(*) AS count FROM employees WHERE {where}", tuple(parameters))[0][
        "count"
    ]
    if selected_order:
        direction = "DESC" if descending is True else "ASC"
        order = f"{selected_order} IS NULL, {selected_order} {direction}, name, emp_id"
    else:
        order = "exit_date, name, emp_id" if exit_view else "name, emp_id"
    rows = query(
        "SELECT emp_id, name, role, department, status, join_date, exit_date FROM employees "
        f"WHERE {where} ORDER BY {order} LIMIT {row_count}",
        tuple(parameters),
    )
    if include_projects is True:
        attach_current_projects(rows)
    filters = with_filters(
        dates,
        role=selected_role,
        department=selected_department,
        employee_status=selected_status,
        unassigned=True if wants_unassigned else None,
        assigned=True if assigned is True else None,
        include_projects=True if include_projects is True else None,
        current_employees_only=True if current_only else None,
        project_name=selected_project,
        client_name=selected_client,
        order_by=selected_order,
        descending=True if selected_order and descending is True else None,
    )
    return success(
        rows,
        total=total,
        truncated=total > len(rows),
        summary=list_summary(total, len(rows)),
        filters=filters,
    )
