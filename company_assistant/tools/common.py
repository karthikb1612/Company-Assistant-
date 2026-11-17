import re
import sqlite3
from collections.abc import Callable
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from typing import Any

from company_assistant.config import get_settings
from company_assistant.db.connection import read_connection

PROJECT_STATUSES = {"active", "completed", "on_hold"}
CLIENT_STATUSES = {"active", "inactive"}
EXIT_STATUSES = ("resigned", "laid_off", "terminated", "retired")
EMPLOYEE_STATUSES = ("active", "bench", *EXIT_STATUSES)
MAX_ROWS = 100
DEFAULT_ROWS = 20
WINDOW_TOOLS = {"get_layoff_count", "get_attrition_stats", "list_projects", "list_new_hires"}
TEXT_FILTERS = (
    "role",
    "client_name",
    "project_name",
    "department",
    "industry",
    "name_contains",
    "name",
)
REQUEST_DATE: ContextVar[date | None] = ContextVar("company_request_date", default=None)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def begin_request() -> None:
    REQUEST_DATE.set(utc_today())


def today() -> date:
    return REQUEST_DATE.get() or utc_today()


def relative_periods(day: date) -> dict[str, dict[str, str]]:
    """Exact inclusive date ranges for common relative expressions, so no model does date math."""
    monday = day - timedelta(days=day.weekday())
    month_start = day.replace(day=1)
    previous_month_end = month_start - timedelta(days=1)
    ranges = {
        "today": (day, day),
        "yesterday": (day - timedelta(days=1), day - timedelta(days=1)),
        "this week": (monday, day),
        "last week": (monday - timedelta(days=7), monday - timedelta(days=1)),
        "this month": (month_start, day),
        "last month": (previous_month_end.replace(day=1), previous_month_end),
        "this year": (day.replace(month=1, day=1), day),
        "last year": (date(day.year - 1, 1, 1), date(day.year - 1, 12, 31)),
        "last 7 days": (day - timedelta(days=6), day),
        "last 30 days": (day - timedelta(days=29), day),
        "last 90 days": (day - timedelta(days=89), day),
    }
    return {
        name: {"start_date": start.isoformat(), "end_date": end.isoformat()}
        for name, (start, end) in ranges.items()
    }


def failure(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "code": code, "message": message}


def safe_tool(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return function(*args, **kwargs)
        except LookupError as error:
            return failure("clarification_required", str(error))
        except ValueError as error:
            return failure("invalid_argument", str(error))
        except (sqlite3.Error, OSError):
            return failure("service_unavailable", "Company data is temporarily unavailable.")

    return wrapped


def success(data: Any, **metadata: Any) -> dict[str, Any]:
    return {"status": "success", "as_of": today().isoformat(), **metadata, "data": data}


def row_limit(value: Any) -> int:
    """Rows to return: 20 by default, up to 100 when the user explicitly asks for more."""
    if value is None:
        return DEFAULT_ROWS
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ROWS:
        raise ValueError(f"limit must be an integer from 1 to {MAX_ROWS}.")
    return value


def list_summary(total: int, shown: int) -> str:
    if total == shown:
        return f"{total} matching in total; all {shown} are listed."
    return (
        f"{total} matching in total; only the first {shown} are listed (partial list). "
        f"Report {total} as the count and offer a filter."
    )


ROLE_ALIASES = {
    "qa": "QA",
    "qa engineer": "QA",
    "quality assurance": "QA",
    "quality assurance engineer": "QA",
    "tester": "QA",
    "sde": "SDE",
    "software engineer": "SDE",
    "software developer": "SDE",
    "software development engineer": "SDE",
    "developer": "SDE",
    "sdet": "SDET",
    "test engineer": "SDET",
    "software development engineer in test": "SDET",
    "devops": "DevOps",
    "devops engineer": "DevOps",
    "data engineer": "Data Engineer",
}


def role_filter(value: Any) -> str:
    """Accept common spellings of the five stored roles; other text is passed through."""
    text = text_filter(value)
    key = " ".join(text.lower().replace("-", " ").split())
    if key not in ROLE_ALIASES and key.endswith("s"):
        key = key[:-1]
    return ROLE_ALIASES.get(key, text)


def employee_id(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"E[0-9]{4}", value.strip().upper()) is None:
        raise ValueError("Use an employee ID such as E1023.")
    return value.strip().upper()


def iso_date(value: Any) -> str:
    """Normalize to YYYY-MM-DD; accepts YYYY-MM-DD or MM-DD-YY."""
    if not isinstance(value, str):
        raise ValueError("Date must be a string in YYYY-MM-DD or MM-DD-YY format.")
    v = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid date: {v!r}.")
        return v
    if re.fullmatch(r"\d{2}-\d{2}-\d{2}", v):
        m, d, y = v.split("-")
        try:
            return date(2000 + int(y), int(m), int(d)).isoformat()
        except ValueError:
            raise ValueError(f"Invalid date: {v!r}.")
    raise ValueError("Date must be in YYYY-MM-DD or MM-DD-YY format.")


def text_filter(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
        raise ValueError("Names and filters must contain 1 to 120 characters.")
    return value.strip()


def like_pattern(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def employee_status_filter(value: Any) -> str:
    status = text_filter(value).lower().replace(" ", "_").replace("-", "_")
    if status not in EMPLOYEE_STATUSES:
        raise ValueError(
            "Employee status must be active, bench, resigned, laid_off, terminated, or retired."
        )
    return status


def effective_year(value: Any) -> int:
    value = today().year if value is None else value
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 9998:
        raise ValueError("Year must be an integer between 1 and 9998.")
    return value


def query(statement: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with read_connection(get_settings().company_db) as connection:
        return [dict(row) for row in connection.execute(statement, parameters)]


def normalize_arguments(name: str, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
    from company_assistant.security.permissions import EMPLOYEE_TOOLS

    canonical = dict(arguments)
    if name in EMPLOYEE_TOOLS:
        target = canonical.get("emp_id")
        if target is None:
            target = state.get("last_emp_id")
        if target is None:
            raise LookupError("Which employee ID should I look up?")
        canonical["emp_id"] = employee_id(target)
    if name in WINDOW_TOOLS and (canonical.get("start_date") is None) != (
        canonical.get("end_date") is None
    ):
        raise ValueError("Provide both start_date and end_date, or neither.")
    if name in {"get_layoff_count", "get_attrition_stats"}:
        if not (canonical.get("start_date") and canonical.get("end_date")):
            canonical["year"] = effective_year(canonical.get("year"))
    for field in (
        "start_date",
        "end_date",
        "joined_start",
        "joined_end",
        "exited_start",
        "exited_end",
    ):
        if field in canonical and canonical[field] is not None:
            canonical[field] = iso_date(canonical[field])
    for first, last in (
        ("start_date", "end_date"),
        ("joined_start", "joined_end"),
        ("exited_start", "exited_end"),
    ):
        if canonical.get(first) and canonical.get(last) and canonical[first] > canonical[last]:
            raise ValueError(f"{first} must be on or before {last}.")
    for field in TEXT_FILTERS:
        if field in canonical and canonical[field] is not None:
            convert = role_filter if field == "role" else text_filter
            canonical[field] = convert(canonical[field])
    if canonical.get("status") is not None:
        status = text_filter(canonical["status"]).lower()
        if status in {"all", "any"}:
            canonical.pop("status")
        elif name == "list_clients":
            if status not in CLIENT_STATUSES:
                raise ValueError("Client status must be active or inactive.")
            canonical["status"] = status
        else:
            if status not in PROJECT_STATUSES:
                raise ValueError("Project status must be active, completed, or on_hold.")
            canonical["status"] = status
    if canonical.get("employee_status") is not None:
        canonical["employee_status"] = employee_status_filter(canonical["employee_status"])
    return canonical
