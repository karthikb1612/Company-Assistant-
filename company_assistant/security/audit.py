import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from company_assistant.security.permissions import Actor

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    user_id TEXT,
    user_role TEXT,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
    reason TEXT NOT NULL,
    correlation_id TEXT NOT NULL
)
"""


AUDITED_ARGUMENTS = frozenset(
    {
        "emp_id",
        "year",
        "role",
        "client_name",
        "status",
        "project_name",
        "start_date",
        "end_date",
        "department",
        "employee_status",
        "joined_start",
        "joined_end",
        "exited_start",
        "exited_end",
        "industry",
        "name_contains",
        "group_by",
        "unassigned",
        "limit",
        "include_departed",
        "include_projects",
        "assigned",
        "order_by",
        "descending",
    }
)


def write_audit(
    path: Path,
    actor: Actor | None,
    tool_name: str,
    arguments: dict[str, Any],
    allowed: bool,
    reason: str,
    correlation_id: str,
) -> None:
    """Record an authorization attempt without prompt, credential, or result content."""
    safe_arguments = {
        key: value if isinstance(value, (int, type(None))) else str(value)[:120]
        for key, value in arguments.items()
        if key in AUDITED_ARGUMENTS
    }
    if isinstance(arguments.get("name"), str):
        safe_arguments["name_length"] = len(arguments["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
        connection.execute(SCHEMA)
        connection.execute(
            "INSERT INTO audit_log (timestamp, user_id, user_role, tool_name, arguments, allowed, reason, correlation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                actor.user_id if actor else None,
                actor.role if actor else None,
                tool_name[:120],
                json.dumps(safe_arguments, sort_keys=True),
                int(allowed),
                reason,
                correlation_id,
            ),
        )
