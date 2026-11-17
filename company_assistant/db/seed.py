import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from company_assistant.security.audit import SCHEMA as AUDIT_SCHEMA

ROLES = ("SDE", "SDET", "QA", "DevOps", "Data Engineer")
CLIENTS = (
    "Acme",
    "Brightwell",
    "Cedar",
    "Delta",
    "Evergreen",
    "Futura",
    "Granite",
    "Harbor",
    "Indigo",
    "Juniper",
)
FIRST_NAMES = (
    "Aarav Priya Rohan Ananya Vikram Meera Arjun Kavya Sanjay Neha "
    "Rahul Isha Karthik Divya Aditya Pooja Nikhil Shreya Varun Anjali "
    "James Emily Michael Sarah David Jessica Daniel Olivia Matthew Sophia "
    "Andrew Hannah Joshua Megan Ryan Laura Brandon Natalie Tyler Rachel "
    "Carlos Sofia Mateo Valentina Diego Camila Lucas Isabella Mei Wei "
    "Hiro Yuki Jin Soo Min Hana Chen Lina Omar Layla "
    "Yusuf Fatima Hassan Amira Ibrahim Noor Liam Emma Noah Ava "
    "Ethan Mia Oliver Chloe Henry Grace Samuel Zoe Nathan Lily "
    "Adrian Elena Marco Giulia Pierre Camille Lars Ingrid Mikhail Anya "
    "Kofi Amara Tariq Zara Felix Clara Hugo Nina Leo Ruby"
).split()
LAST_NAMES = (
    "Sharma Patel Iyer Reddy Nair Gupta Mehta Kapoor Rao Menon "
    "Singh Verma Joshi Desai Banerjee Chopra Malhotra Bhat Kulkarni Pillai "
    "Smith Johnson Williams Brown Jones Miller Davis Wilson Anderson Taylor "
    "Thomas Moore Martin Jackson Thompson White Harris Clark Lewis Walker "
    "Garcia Martinez Rodriguez Hernandez Lopez Gonzalez Perez Sanchez Ramirez Torres "
    "Flores Rivera Gomez Diaz Morales Ortiz Wang Li Zhang Liu "
    "Yang Huang Zhao Wu Zhou Xu Tanaka Suzuki Sato Kim "
    "Park Choi Haddad Nasser Khalil Farouk Okafor Mensah Adeyemi Osei "
    "Novak Kowalski Petrov Ivanov Larsen Dubois Rossi Weber Foster Murphy "
    "Bennett Hughes Cooper Reed Bailey Ward Brooks Gray Price Coleman"
).split()


def person_name(number: int) -> str:
    """Return a unique fictional full name for each employee number below 10,000."""
    scrambled = (number * 37) % 10000
    return f"{FIRST_NAMES[scrambled % 100]} {LAST_NAMES[scrambled // 100]}"


def person_email(number: int) -> str:
    first, last = person_name(number).lower().split()
    return f"{first}.{last}@example.invalid"


def build_seed(as_of: date) -> dict[str, list[dict[str, Any]]]:
    """Build reproducible fictional records without IO, randomness, or a clock."""
    employees = []
    statuses = ["active"] * 50 + ["bench"] * 10 + ["resigned"] * 10 + ["laid_off"] * 5
    statuses += ["terminated"] * 3 + ["retired"] * 2
    for position, status in enumerate(statuses, 1):
        exit_date = None
        if status not in {"active", "bench"}:
            exit_date = date(as_of.year - position % 2, min(as_of.month, 6), min(as_of.day, 15))
        employees.append(
            {
                "emp_id": f"E{1000 + position}",
                "name": person_name(1000 + position),
                "email": person_email(1000 + position),
                "role": ROLES[(position - 1) % 5],
                "department": "People" if position == 1 else "Engineering",
                "prior_experience_years": float(position % 9),
                "join_date": (as_of - timedelta(days=2200 + position)).isoformat(),
                "status": status,
                "bench_since": (as_of - timedelta(days=60)).isoformat()
                if status == "bench"
                else None,
                "exit_date": exit_date.isoformat() if exit_date else None,
                "exit_reason": status if exit_date else None,
                "manager_id": None if position <= 3 else ("E1002" if position <= 40 else "E1003"),
                "app_role": "hr"
                if position == 1
                else ("manager" if position in (2, 3) else "employee"),
            }
        )
    clients = [
        {
            "client_id": position,
            "name": name,
            "industry": "Technology",
            "start_date": (as_of - timedelta(days=1800)).isoformat(),
            "status": "inactive" if position == 10 else "active",
        }
        for position, name in enumerate(CLIENTS, 1)
    ]
    projects = [
        {
            "project_id": position,
            "name": f"{CLIENTS[(position - 1) // 2]} Project {position:02d}",
            "client_id": (position - 1) // 2 + 1,
            "status": "active"
            if position <= 16
            else ("on_hold" if position <= 18 else "completed"),
            "start_date": (as_of - timedelta(days=1500)).isoformat(),
            "end_date": (as_of - timedelta(days=90)).isoformat() if position > 18 else None,
        }
        for position in range(1, 21)
    ]
    assignments = []
    for position, employee in enumerate(employees, 1):
        if position == 49:
            continue
        assignments.append(
            {
                "emp_id": employee["emp_id"],
                "project_id": 19,
                "start_date": (as_of - timedelta(days=1200)).isoformat(),
                "end_date": (as_of - timedelta(days=1000)).isoformat(),
                "role_on_project": employee["role"],
            }
        )
        if employee["status"] == "active":
            assignments.append(
                {
                    "emp_id": employee["emp_id"],
                    "project_id": (position - 1) % 16 + 1,
                    "start_date": (as_of - timedelta(days=365)).isoformat(),
                    "end_date": None,
                    "role_on_project": employee["role"],
                }
            )
    assignments.append(
        {
            "emp_id": "E1023",
            "project_id": 19,
            "start_date": (as_of - timedelta(days=900)).isoformat(),
            "end_date": (as_of - timedelta(days=800)).isoformat(),
            "role_on_project": "QA",
        }
    )
    return {
        "employees": employees,
        "clients": clients,
        "projects": projects,
        "assignments": assignments,
    }


def _insert_records(connection: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    columns = ", ".join(rows[0])
    placeholders = ", ".join(f":{column}" for column in rows[0])
    connection.executemany(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", rows)


def _validate_batch_range(count: int, first_number: int) -> None:
    if (
        type(count) is not int
        or type(first_number) is not int
        or not 0 <= first_number <= 9999
        or not 1 <= count <= 10000 - first_number
    ):
        raise ValueError(
            "Count must be positive and fit within the available four-digit employee IDs."
        )


def build_dummy_batch(
    as_of: date, count: int, first_number: int, manager_ids: tuple[str, ...]
) -> dict[str, list[dict[str, Any]]]:
    """Build deterministic employees and explicitly synthetic audit records without IO.

    Date distribution: 20 % of employees get a recent join date within the last 76 days
    (covering window-based test queries for new hires), 80 % get historical dates spread
    across 1–5 years ago.  Exit dates are spread across the last two years so that date-
    window queries for exits and layoffs return real results rather than empty sets.
    """
    _validate_batch_range(count, first_number)
    statuses = ["active"] * 50 + ["bench"] * 10 + ["resigned"] * 10 + ["laid_off"] * 5
    statuses += ["terminated"] * 3 + ["retired"] * 2
    employees = []
    audit_records = []
    midnight = datetime(as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc)
    batch = f"dummy-seed:{as_of}:E{first_number:04d}-E{first_number + count - 1:04d}"
    for offset in range(count):
        target = f"E{first_number + offset:04d}"
        status = statuses[offset % len(statuses)]
        role = ROLES[offset % len(ROLES)]
        # 20 % recent (within last 76 days), 80 % historical (1–5 years ago).
        if offset % 10 < 2:
            join_days_ago = max(1, offset % 76)
        else:
            join_days_ago = 400 + (offset * 17) % 1400
        join_date = as_of - timedelta(days=join_days_ago)
        bench_since = None
        exit_date = None
        if status == "bench":
            bench_days = max(1, (offset * 7) % 150)
            bench_dt = as_of - timedelta(days=bench_days)
            if bench_dt < join_date:
                bench_dt = join_date + timedelta(days=1)
            bench_since = bench_dt.isoformat()
        elif status not in {"active"}:
            # Spread exits across the last ~2 years so window queries return results.
            exit_days_ago = 10 + (offset * 11) % 700
            exit_dt = as_of - timedelta(days=exit_days_ago)
            if exit_dt <= join_date:
                exit_dt = join_date + timedelta(days=30)
            exit_date = exit_dt.isoformat()
        employees.append(
            {
                "emp_id": target,
                "name": person_name(first_number + offset),
                "email": person_email(first_number + offset),
                "role": role,
                "department": "Engineering",
                "prior_experience_years": float(offset % 9),
                "join_date": join_date.isoformat(),
                "status": status,
                "bench_since": bench_since,
                "exit_date": exit_date,
                "exit_reason": status if exit_date else None,
                "manager_id": manager_ids[offset % len(manager_ids)] if manager_ids else None,
                "app_role": "employee",
            }
        )
        allowed = status in {"active", "bench"}
        audit_records.append(
            {
                "timestamp": (midnight + timedelta(seconds=offset)).isoformat(),
                "user_id": target,
                "user_role": "employee",
                "tool_name": "get_employee_profile",
                "arguments": json.dumps({"emp_id": target}, sort_keys=True),
                "allowed": int(allowed),
                "reason": "dummy_allowed" if allowed else "dummy_denied",
                "correlation_id": f"{batch}:{offset + 1}",
            }
        )
    return {"employees": employees, "audit_log": audit_records}


def append_dummy_data(company_db: Path, audit_db: Path, as_of: date, count: int) -> dict[str, Any]:
    """Append a synthetic batch to both files in a seed-only transaction."""
    _validate_batch_range(count, 0)
    company_db, audit_db = company_db.resolve(), audit_db.resolve()
    if company_db == audit_db:
        raise ValueError("Company and audit databases must be separate files.")
    if not company_db.is_file():
        raise FileNotFoundError("Seed the company database before appending dummy records.")
    audit_db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(audit_db, timeout=5)) as audit_connection, audit_connection:
        audit_connection.execute(AUDIT_SCHEMA)
    with closing(
        sqlite3.connect(company_db.as_uri() + "?mode=rw", uri=True, timeout=5)
    ) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("ATTACH DATABASE ? AS seed_audit", (audit_db.as_uri() + "?mode=rw",))
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            first_number = connection.execute(
                "SELECT COALESCE(MAX(CAST(SUBSTR(emp_id, 2) AS INTEGER)), 1000) + 1 "
                "FROM employees WHERE emp_id GLOB 'E[0-9][0-9][0-9][0-9]'"
            ).fetchone()[0]
            manager_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT emp_id FROM employees WHERE app_role = 'manager' "
                    "AND status IN ('active', 'bench') ORDER BY emp_id"
                )
            )
            records = build_dummy_batch(as_of, count, first_number, manager_ids)
            _insert_records(connection, "employees", records["employees"])
            _insert_records(connection, "seed_audit.audit_log", records["audit_log"])
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Company data contains invalid foreign-key relationships.")
    return {
        "employees_added": count,
        "audit_records_added": count,
        "first_emp_id": records["employees"][0]["emp_id"],
        "last_emp_id": records["employees"][-1]["emp_id"],
    }


def seed_database(path: Path, as_of: date, *, reset: bool = False) -> None:
    """Create a synthetic database; replacement requires explicit reset."""
    if path.exists() and not reset:
        raise FileExistsError("Database already exists; use --reset only for synthetic data.")
    records = build_seed(as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".db")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary_path)) as connection, connection:
            connection.executescript(
                Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            )
            for table, rows in records.items():
                _insert_records(connection, table, rows)
            connection.execute("INSERT INTO dataset_metadata VALUES (?)", (as_of.isoformat(),))
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    from company_assistant.config import get_settings, load_environment

    load_environment()
    parser = argparse.ArgumentParser(
        description="Create synthetic company data (never use real HR data)."
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=datetime.now(timezone.utc).date()
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--reset",
        action="store_true",
        help="Explicitly replace synthetic company data, not audit history.",
    )
    mode.add_argument(
        "--append-count",
        type=int,
        help="Append this many dummy employees and marked audit records, preserving existing data.",
    )
    arguments = parser.parse_args()
    settings = get_settings()
    if arguments.append_count is not None:
        try:
            result = append_dummy_data(
                settings.company_db, settings.audit_db, arguments.as_of, arguments.append_count
            )
        except (sqlite3.Error, OSError, ValueError):
            parser.exit(
                1,
                "Could not append dummy data; check paths, schema, available IDs, count, and database locks.\n",
            )
        print(
            f"Added {result['employees_added']} dummy employees and {result['audit_records_added']} dummy audit records. "
            f"Employee IDs: {result['first_emp_id']} through {result['last_emp_id']}."
        )
        return
    try:
        seed_database(settings.company_db, arguments.as_of, reset=arguments.reset)
    except FileExistsError as error:
        parser.exit(1, str(error) + "\n")
    print(f"Seeded 80 synthetic employees, 10 clients, 20 projects as of {arguments.as_of}.")
    print(f"Database: {settings.company_db}")


if __name__ == "__main__":
    main()
