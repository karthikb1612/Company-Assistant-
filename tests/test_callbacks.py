import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from company_assistant.config import Settings
from company_assistant.db.seed import seed_database
from company_assistant.security.callbacks import check_permissions


class CallbackIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        seed_database(self.path / "company.db", date(2026, 11, 16))
        self.settings = Settings(self.path / "company.db", self.path / "audit.db", "E1023")
        self.context = SimpleNamespace(
            state={"user_id": "E1001", "user_role": "hr"}, invocation_id="test"
        )
        patcher = patch(
            "company_assistant.security.callbacks.get_settings", side_effect=lambda: self.settings
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, **arguments):
        return check_permissions(SimpleNamespace(name=name), arguments, self.context), arguments

    def test_forged_state_denied_and_audited(self):
        result, _ = self.call("get_employee_profile", emp_id="E1050")
        self.assertEqual(result["code"], "permission_denied")
        with closing(sqlite3.connect(self.settings.audit_db)) as connection:
            record = connection.execute(
                "SELECT user_id, user_role, allowed FROM audit_log"
            ).fetchone()
        self.assertEqual(record, ("E1023", "employee", 0))

    def test_canonical_target_and_follow_up(self):
        result, arguments = self.call("get_employee_profile", emp_id=" e1023 ")
        self.assertIsNone(result)
        self.assertEqual(arguments["emp_id"], "E1023")
        self.context.state["last_emp_id"] = "E1023"
        result, arguments = self.call("get_employee_projects")
        self.assertIsNone(result)
        self.assertEqual(arguments["emp_id"], "E1023")

    def test_denial_clears_previous_target(self):
        self.context.state["last_emp_id"] = "E1023"
        self.call("get_employee_profile", emp_id="E1050")
        self.assertIsNone(self.context.state.get("last_emp_id"))

    def test_missing_context_requests_clarification(self):
        result, _ = self.call("get_employee_profile")
        self.assertEqual(result["code"], "clarification_required")

    def test_manager_direct_reports_only(self):
        self.settings = Settings(self.settings.company_db, self.settings.audit_db, "E1002")
        self.assertIsNone(self.call("get_employee_profile", emp_id="E1023")[0])
        self.assertEqual(
            self.call("get_employee_profile", emp_id="E1050")[0]["code"], "permission_denied"
        )

    def as_actor(self, actor):
        self.settings = Settings(self.settings.company_db, self.settings.audit_db, actor)

    def test_email_manager_and_reports_follow_profile_access(self):
        for tool in ("get_employee_email", "get_employee_manager", "list_direct_reports"):
            with self.subTest(tool=tool):
                self.as_actor("E1023")
                self.assertIsNone(self.call(tool, emp_id="E1023")[0])
                self.assertEqual(self.call(tool, emp_id="E1002")[0]["code"], "permission_denied")
                self.as_actor("E1002")
                self.assertIsNone(self.call(tool, emp_id="E1002")[0])
                self.assertIsNone(self.call(tool, emp_id="E1023")[0])
                self.assertEqual(self.call(tool, emp_id="E1050")[0]["code"], "permission_denied")
                self.as_actor("E1001")
                self.assertIsNone(self.call(tool, emp_id="E1050")[0])

    def test_lists_and_rosters_require_manager_or_hr(self):
        for tool, arguments in (
            ("list_employees", {"role": "QA"}),
            ("get_project_team", {"project_name": "Delta Project 07"}),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(self.call(tool, **arguments)[0]["code"], "permission_denied")
                for actor in ("E1002", "E1001"):
                    self.as_actor(actor)
                    self.assertIsNone(self.call(tool, **arguments)[0])
                self.as_actor("E1023")

    def test_name_search_is_audited_by_length_only(self):
        result = check_permissions(
            SimpleNamespace(name="find_employee"), {"name": "Henry Park"}, self.context
        )
        self.assertIsNone(result)
        with closing(sqlite3.connect(self.settings.audit_db)) as connection:
            arguments = connection.execute("SELECT arguments FROM audit_log").fetchone()[0]
        self.assertEqual(json.loads(arguments), {"name_length": 10})
        self.assertNotIn("Henry", arguments)

    def test_list_constraints_are_audited(self):
        self.as_actor("E1001")
        result, _ = self.call(
            "list_employees",
            project_name="Delta Project 07",
            client_name="Delta",
            order_by="join_date",
            descending=True,
            assigned=True,
        )
        self.assertIsNone(result)
        with closing(sqlite3.connect(self.settings.audit_db)) as connection:
            arguments = json.loads(
                connection.execute("SELECT arguments FROM audit_log").fetchone()[0]
            )
        self.assertEqual(arguments["project_name"], "Delta Project 07")
        self.assertEqual(arguments["client_name"], "Delta")
        self.assertEqual(arguments["order_by"], "join_date")
        self.assertEqual(arguments["descending"], True)
        self.assertEqual(arguments["assigned"], True)

    def test_half_supplied_window_is_rejected(self):
        self.as_actor("E1001")
        for tool, arguments in (
            ("get_attrition_stats", {"start_date": "2026-01-01"}),
            ("get_layoff_count", {"end_date": "2026-06-30"}),
            ("list_projects", {"start_date": "2026-01-01"}),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(self.call(tool, **arguments)[0]["code"], "invalid_argument")

    def test_unknown_inactive_and_missing_actors(self):
        for actor in ("", "E9999", "E1080"):
            self.settings = Settings(self.settings.company_db, self.settings.audit_db, actor)
            with self.subTest(actor=actor):
                self.assertEqual(self.call("get_headcount")[0]["code"], "permission_denied")

    def test_audit_failure_blocks_execution(self):
        with patch(
            "company_assistant.security.callbacks.write_audit",
            side_effect=sqlite3.OperationalError("private path"),
        ):
            result, _ = self.call("get_headcount")
        self.assertEqual(result["code"], "service_unavailable")
        self.assertNotIn("private", json.dumps(result))

    def test_wrapper_request_is_not_logged(self):
        self.call("workforce_agent", request="do not log this prompt")
        with closing(sqlite3.connect(self.settings.audit_db)) as connection:
            arguments = connection.execute("SELECT arguments FROM audit_log").fetchone()[0]
        self.assertEqual(json.loads(arguments), {})

    def test_unknown_tool_denied(self):
        self.assertEqual(self.call("execute_sql", sql="SELECT *")[0]["code"], "permission_denied")

    def test_invalid_target_is_safe(self):
        self.assertEqual(
            self.call("get_employee_profile", emp_id="' OR 1=1 --")[0]["code"], "invalid_argument"
        )
