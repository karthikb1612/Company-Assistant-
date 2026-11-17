import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from company_assistant.db.connection import read_connection
from company_assistant.db.seed import (
    append_dummy_data,
    build_dummy_batch,
    build_seed,
    seed_database,
)
from company_assistant.security.audit import SCHEMA


class SeedUnitTests(unittest.TestCase):
    def test_seed_is_deterministic(self):
        records = build_seed(date(2026, 11, 16))
        self.assertEqual(records, build_seed(date(2026, 11, 16)))
        self.assertEqual(len(records["employees"]), 80)
        self.assertEqual(len(records["clients"]), 10)
        self.assertEqual(len(records["projects"]), 20)

    def test_dummy_batch_is_deterministic_and_marked(self):
        arguments = (date(2026, 11, 16), 2000, 1081, ("E1002", "E1003"))
        records = build_dummy_batch(*arguments)
        self.assertEqual(records, build_dummy_batch(*arguments))
        self.assertEqual(len(records["employees"]), 2000)
        self.assertEqual(len(records["audit_log"]), 2000)
        self.assertEqual(records["employees"][0]["emp_id"], "E1081")
        self.assertEqual(records["employees"][-1]["emp_id"], "E3080")
        self.assertTrue(all(row["app_role"] == "employee" for row in records["employees"]))
        self.assertEqual({row["manager_id"] for row in records["employees"]}, {"E1002", "E1003"})
        self.assertEqual({row["allowed"] for row in records["audit_log"]}, {0, 1})
        self.assertTrue(all(row["reason"].startswith("dummy_") for row in records["audit_log"]))
        self.assertTrue(
            all(row["correlation_id"].startswith("dummy-seed:") for row in records["audit_log"])
        )

    def test_employee_names_and_emails_are_unique_and_not_placeholders(self):
        seeded = build_seed(date(2026, 11, 16))["employees"]
        batch = build_dummy_batch(date(2026, 11, 16), 2000, 1081, ())["employees"]
        rows = seeded + batch
        self.assertEqual(len({row["name"] for row in rows}), len(rows))
        self.assertEqual(len({row["email"] for row in rows}), len(rows))
        self.assertTrue(all(row["email"].endswith("@example.invalid") for row in rows))
        self.assertFalse(
            any(word in row["name"] for row in rows for word in ("Demo", "Dummy", "Employee"))
        )

    def test_invalid_batch_sizes_and_id_exhaustion(self):
        for count in (0, -1, True, 2.5, 10001):
            with self.subTest(count=count), self.assertRaises(ValueError):
                build_dummy_batch(date(2026, 11, 16), count, 1081, ())
        with self.assertRaises(ValueError):
            build_dummy_batch(date(2026, 11, 16), 2000, 9000, ())

    def test_dummy_batch_without_managers(self):
        records = build_dummy_batch(date(2026, 11, 16), 1, 1081, ())
        self.assertIsNone(records["employees"][0]["manager_id"])


class DatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "company # demo.db"
        seed_database(self.path, date(2026, 11, 16))

    def create_audit_fixture(self):
        audit_path = self.path.with_name("audit.db")
        with closing(sqlite3.connect(audit_path)) as connection, connection:
            connection.execute(SCHEMA)
            connection.execute(
                "INSERT INTO audit_log (timestamp, user_id, user_role, tool_name, arguments, allowed, reason, correlation_id) "
                "VALUES ('2026-11-16T00:00:00+00:00', 'E1001', 'hr', 'get_headcount', '{}', 1, 'allowed', 'existing-record')"
            )
        return audit_path

    def snapshot(self, path, table, order):
        with read_connection(path) as connection:
            return [
                tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")
            ]

    def test_append_preserves_existing_data_and_relationships(self):
        audit_path = self.create_audit_fixture()
        original_employees = self.snapshot(self.path, "employees", "emp_id")
        original_audit = self.snapshot(audit_path, "audit_log", "id")
        original_assignments = self.snapshot(self.path, "assignments", "assignment_id")
        result = append_dummy_data(self.path, audit_path, date(2026, 11, 16), 2000)
        self.assertEqual(
            result,
            {
                "employees_added": 2000,
                "audit_records_added": 2000,
                "first_emp_id": "E1081",
                "last_emp_id": "E3080",
            },
        )
        employees = self.snapshot(self.path, "employees", "emp_id")
        audit = self.snapshot(audit_path, "audit_log", "id")
        self.assertEqual(len(employees), 2080)
        self.assertEqual(len(audit), 2001)
        self.assertEqual(employees[:80], original_employees)
        self.assertEqual(audit[:1], original_audit)
        self.assertEqual(
            self.snapshot(self.path, "assignments", "assignment_id"), original_assignments
        )
        with read_connection(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        second = append_dummy_data(self.path, audit_path, date(2026, 11, 16), 2)
        self.assertEqual(second["first_emp_id"], "E3081")
        self.assertEqual(len(self.snapshot(self.path, "employees", "emp_id")), 2082)

    def test_append_rolls_back_both_databases_on_audit_failure(self):
        audit_path = self.create_audit_fixture()
        with closing(sqlite3.connect(audit_path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_dummy BEFORE INSERT ON audit_log "
                "WHEN NEW.reason LIKE 'dummy_%' AND (SELECT COUNT(*) FROM audit_log) >= 2 "
                "BEGIN SELECT RAISE(ABORT, 'test rejection'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            append_dummy_data(self.path, audit_path, date(2026, 11, 16), 3)
        self.assertEqual(len(self.snapshot(self.path, "employees", "emp_id")), 80)
        self.assertEqual(len(self.snapshot(audit_path, "audit_log", "id")), 1)

    def test_append_rejects_broken_existing_relationships(self):
        audit_path = self.create_audit_fixture()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE employees SET manager_id = 'E9999' WHERE emp_id = 'E1023'")
        with self.assertRaises(
            ValueError, msg="Invalid existing relationships must abort the batch"
        ):
            append_dummy_data(self.path, audit_path, date(2026, 11, 16), 2)
        self.assertEqual(len(self.snapshot(self.path, "employees", "emp_id")), 80)
        self.assertEqual(len(self.snapshot(audit_path, "audit_log", "id")), 1)

    def test_append_creates_audit_when_missing(self):
        audit_path = self.path.with_name("new-audit.db")
        append_dummy_data(self.path, audit_path, date(2026, 11, 16), 2)
        self.assertEqual(len(self.snapshot(audit_path, "audit_log", "id")), 2)

    def test_append_rejects_same_file_and_missing_company(self):
        with self.assertRaises(ValueError):
            append_dummy_data(self.path, self.path, date(2026, 11, 16), 2)
        with self.assertRaises(FileNotFoundError):
            append_dummy_data(
                self.path.with_name("missing.db"),
                self.path.with_name("audit.db"),
                date(2026, 11, 16),
                2,
            )

    def test_expected_counts_and_foreign_keys(self):
        with read_connection(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            counts = dict(
                connection.execute("SELECT status, COUNT(*) FROM employees GROUP BY status")
            )
            self.assertEqual(
                counts,
                {
                    "active": 50,
                    "bench": 10,
                    "resigned": 10,
                    "laid_off": 5,
                    "terminated": 3,
                    "retired": 2,
                },
            )

    def test_runtime_rejects_all_writes(self):
        statements = [
            "DELETE FROM employees",
            "UPDATE employees SET name = 'changed'",
            "CREATE TABLE forbidden (value TEXT)",
            "INSERT INTO clients VALUES (99, 'Bad', 'Bad', '2020-01-01', 'active')",
        ]
        with read_connection(self.path) as connection:
            for statement in statements:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.OperationalError):
                    connection.execute(statement)

    def test_seed_requires_explicit_reset(self):
        with self.assertRaises(FileExistsError):
            seed_database(self.path, date(2026, 11, 16))
        seed_database(self.path, date(2026, 11, 16), reset=True)

    def test_missing_database_does_not_get_created(self):
        missing = self.path.with_name("missing.db")
        with self.assertRaises(sqlite3.OperationalError), read_connection(missing):
            pass
        self.assertFalse(missing.exists())


@pytest.mark.integration
def test_append_cli_and_invalid_modes(seeded, monkeypatch, capsys):
    from company_assistant.db.seed import main

    monkeypatch.setattr("sys.argv", ["seed", "--as-of", "2026-11-16", "--append-count", "2"])
    main()
    assert "Added 2 dummy employees and 2 dummy audit records" in capsys.readouterr().out
    for arguments in (["seed", "--append-count", "0"], ["seed", "--append-count", "2", "--reset"]):
        monkeypatch.setattr("sys.argv", arguments)
        with pytest.raises(SystemExit) as result:
            main()
        assert result.value.code != 0
    with read_connection(seeded[0]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 82
    with read_connection(seeded[1]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 2
