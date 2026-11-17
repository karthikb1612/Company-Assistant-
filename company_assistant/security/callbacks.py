import logging
import sqlite3
from typing import Any

from company_assistant.config import Settings, get_settings
from company_assistant.db.connection import read_connection
from company_assistant.security.audit import write_audit
from company_assistant.security.permissions import EMPLOYEE_TOOLS, Actor, can_call
from company_assistant.tools.common import begin_request, failure, normalize_arguments, today

logger = logging.getLogger(__name__)


def resolve_actor(settings: Settings) -> Actor | None:
    with read_connection(settings.company_db) as connection:
        row = connection.execute(
            "SELECT emp_id, app_role FROM employees WHERE emp_id = ? AND status IN ('active', 'bench')",
            (settings.demo_user_id,),
        ).fetchone()
    return Actor(row["emp_id"], row["app_role"]) if row else None


def check_permissions(tool: Any, args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    """Canonicalize, authorize, then audit; only None permits ADK execution."""
    actor = None
    result = None
    try:
        settings = get_settings()
        actor = resolve_actor(settings)
        try:
            canonical = normalize_arguments(tool.name, args, tool_context.state)
            args.clear()
            args.update(canonical)
        except LookupError as error:
            result = failure("clarification_required", str(error))
        except ValueError as error:
            result = failure("invalid_argument", str(error))
        manager_id = None
        if result is None and tool.name in EMPLOYEE_TOOLS and actor and actor.role == "manager":
            with read_connection(settings.company_db) as connection:
                target = connection.execute(
                    "SELECT manager_id FROM employees WHERE emp_id = ?", (args["emp_id"],)
                ).fetchone()
            manager_id = target["manager_id"] if target else None
        if result is None and not can_call(actor, tool.name, args.get("emp_id"), manager_id):
            result = failure(
                "permission_denied", "You do not have permission to access that information."
            )
        write_audit(
            settings.audit_db,
            actor,
            tool.name,
            args,
            result is None,
            result["code"] if result else "allowed",
            str(tool_context.invocation_id),
        )
    except (sqlite3.Error, OSError, ValueError):
        logger.error("Authorization or audit service unavailable; tool execution blocked.")
        result = failure(
            "service_unavailable",
            "Company data is unavailable. Check local database and audit configuration.",
        )
    if result is not None and tool.name in EMPLOYEE_TOOLS:
        tool_context.state["last_emp_id"] = None
        tool_context.state["lookup_failed"] = True
    return result


def load_user_context(callback_context: Any) -> Any:
    """Refresh informational identity mirrors from trusted process configuration."""
    from google.genai import types

    try:
        actor = resolve_actor(get_settings())
        if actor is None or actor.role not in {"employee", "manager", "hr"}:
            raise ValueError("Invalid demo identity")
    except (sqlite3.Error, OSError, ValueError):
        return types.Content(
            role="model",
            parts=[
                types.Part(
                    text="Configure a valid DEMO_USER_ID and seed the synthetic database before chatting."
                )
            ],
        )
    if callback_context.agent_name == "company_assistant":
        begin_request()
    callback_context.state["user_id"] = actor.user_id
    callback_context.state["user_role"] = actor.role
    callback_context.state["temp:as_of"] = today().isoformat()
    if callback_context.agent_name == "company_assistant":
        for key in callback_context.state.to_dict():
            if key.startswith("lookup_target:"):
                callback_context.state[key] = False
        callback_context.state["lookup_failed"] = False
    return None
