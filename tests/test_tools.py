import sqlite3

import pytest

from company_assistant.tools import client_project_tools, employee_tools, workforce_tools


@pytest.mark.integration
def test_headcount_includes_bench(seeded):
    result = workforce_tools.get_headcount()
    assert result["data"] == {"total": 60, "active": 50, "bench": 10}
    assert result["as_of"] == "2026-11-16"


@pytest.mark.integration
def test_profile_counts_distinct_projects_and_remembers_target(seeded, context):
    result = employee_tools.get_employee_profile(context, "E1023")
    assert result["data"]["project_count"] == 2
    assert result["data"]["years_at_company"] == 6.09
    assert result["data"]["total_experience_years"] == 11.09
    assert context.state["last_emp_id"] == "E1023"
    assert not {"email", "exit_reason", "app_role", "manager_id"} & result["data"].keys()


@pytest.mark.integration
def test_profile_not_found_clears_previous_target(seeded, context):
    context.state["last_emp_id"] = "E1023"
    assert employee_tools.get_employee_profile(context, "E9999")["code"] == "not_found"
    assert context.state.get("last_emp_id") is None


@pytest.mark.integration
def test_employee_with_no_projects(seeded, context):
    assert employee_tools.get_employee_profile(context, "E1049")["data"]["project_count"] == 0


def test_query_failure_is_sanitized(monkeypatch):
    def unavailable(*args):
        raise sqlite3.OperationalError("secret server path")

    monkeypatch.setattr(workforce_tools, "query", unavailable)
    result = workforce_tools.get_headcount()
    assert result["code"] == "service_unavailable"
    assert "secret" not in str(result)


@pytest.mark.integration
@pytest.mark.parametrize("year,expected", [(2025, 3), (2026, 2), (None, 2), (2024, 0)])
def test_layoffs_by_exit_year(seeded, year, expected):
    result = workforce_tools.get_layoff_count(year)
    assert result["data"]["count"] == expected
    assert result["filters"]["year"] == (year or 2026)


@pytest.mark.integration
def test_attrition_breakdown(seeded):
    result = workforce_tools.get_attrition_stats()
    assert result["data"] == {
        "total": 10,
        "breakdown": {"resigned": 5, "laid_off": 2, "terminated": 2, "retired": 1},
    }


@pytest.mark.integration
def test_bench_counts_and_filtered_names(seeded):
    result = workforce_tools.get_bench_summary()
    assert result["data"]["total"] == 10
    assert set(result["data"]["by_role"].values()) == {2}
    assert len(workforce_tools.list_bench_employees(" sde ")["data"]) == 2
    assert workforce_tools.list_bench_employees("Unknown role")["data"] == []


@pytest.mark.integration
def test_clients_and_project_filters(seeded):
    assert client_project_tools.get_client_count()["data"] == {
        "total": 10,
        "active": 9,
        "inactive": 1,
    }
    assert len(client_project_tools.list_clients()["data"]) == 10
    assert len(client_project_tools.list_projects()["data"]) == 20
    assert len(client_project_tools.list_projects(" acme ")["data"]) == 2
    assert client_project_tools.list_projects("Juniper", "active")["data"] == []
    assert client_project_tools.list_projects("Unknown")["code"] == "not_found"
    assert client_project_tools.list_projects(status="invalid")["code"] == "invalid_argument"


@pytest.mark.integration
def test_project_details_count_current_team_only(seeded):
    result = client_project_tools.get_project_details(" delta project 07 ")
    assert result["data"]["team_size"] == 3
    assert "employees" not in result["data"]
    assert client_project_tools.get_project_details("Juniper Project 19")["data"]["team_size"] == 0
    assert client_project_tools.get_project_details("Unknown")["code"] == "not_found"


@pytest.mark.integration
def test_project_history_groups_repeated_stints(seeded, context):
    result = employee_tools.get_employee_projects(context, "E1023")
    assert len(result["data"]) == 2
    assert sorted(len(project["assignments"]) for project in result["data"]) == [1, 2]
    assert employee_tools.get_employee_projects(context, "E1049")["data"] == []
    assert employee_tools.get_employee_projects(context, "E9999")["code"] == "not_found"


@pytest.mark.integration
def test_departed_experience_stops_at_exit_date(seeded, context):
    from datetime import date

    profile = employee_tools.get_employee_profile(context, "E1080")["data"]
    expected = (
        date.fromisoformat(profile["exit_date"]) - date.fromisoformat(profile["join_date"])
    ).days / 365.2425
    assert profile["years_at_company"] == round(expected, 2)


@pytest.mark.parametrize("value", [True, "2025", 0, 9999, -1, 2025.5])
def test_invalid_year_rejected_before_io(value):
    assert workforce_tools.get_layoff_count(value)["code"] == "invalid_argument"


@pytest.mark.parametrize("value", ["", " ", "x" * 121, 5])
def test_invalid_project_name(value):
    assert client_project_tools.get_project_details(value)["code"] == "invalid_argument"


def test_missing_employee_context(context):
    assert employee_tools.get_employee_profile(context)["code"] == "clarification_required"
    assert employee_tools.get_employee_profile(context, "invalid")["code"] == "invalid_argument"


@pytest.mark.integration
def test_seed_cli_requires_reset_and_preserves_audit(seeded, monkeypatch, capsys):
    from company_assistant.db.seed import main

    seeded[1].write_bytes(b"audit sentinel")
    monkeypatch.setattr("sys.argv", ["seed", "--as-of", "2026-11-16"])
    with pytest.raises(SystemExit):
        main()
    monkeypatch.setattr("sys.argv", ["seed", "--as-of", "2026-11-16", "--reset"])
    main()
    assert "80 synthetic" in capsys.readouterr().out
    assert seeded[1].read_bytes() == b"audit sentinel"


def test_database_paths_must_differ(monkeypatch):
    from company_assistant.config import get_settings

    monkeypatch.setenv("COMPANY_DB_PATH", "same.db")
    monkeypatch.setenv("AUDIT_DB_PATH", "same.db")
    with pytest.raises(ValueError, match="separate"):
        get_settings()


def test_default_databases_live_in_package_db_directory(monkeypatch):
    from pathlib import Path

    from company_assistant import config

    monkeypatch.delenv("COMPANY_DB_PATH", raising=False)
    monkeypatch.delenv("AUDIT_DB_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "ignored-local-app-data")
    directory = Path(config.__file__).resolve().parent / "db"
    settings = config.get_settings()
    assert settings.company_db == directory / "company.db"
    assert settings.audit_db == directory / "audit.db"


def test_explicit_database_paths_override_package_defaults(monkeypatch):
    from pathlib import Path

    from company_assistant.config import get_settings

    monkeypatch.setenv("COMPANY_DB_PATH", "custom/company.db")
    monkeypatch.setenv("AUDIT_DB_PATH", "custom/audit.db")
    settings = get_settings()
    assert settings.company_db == Path("custom/company.db").resolve()
    assert settings.audit_db == Path("custom/audit.db").resolve()


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("get_layoff_count", {}, {"year": 2026}),
        ("list_bench_employees", {"role": " QA "}, {"role": "QA"}),
        ("list_projects", {"status": " ACTIVE "}, {"status": "active"}),
    ],
)
def test_callback_argument_normalization(monkeypatch, name, arguments, expected):
    from datetime import date

    from company_assistant.tools.common import normalize_arguments

    monkeypatch.setattr("company_assistant.tools.common.today", lambda: date(2026, 11, 16))
    assert normalize_arguments(name, arguments, {}) == expected


def test_callback_rejects_bad_status():
    from company_assistant.tools.common import normalize_arguments

    with pytest.raises(ValueError, match="Project status"):
        normalize_arguments("list_projects", {"status": "bogus"}, {})


def test_request_date_is_stable_across_midnight(monkeypatch):
    from datetime import date

    from company_assistant.tools import common

    token = common.REQUEST_DATE.set(None)
    try:
        monkeypatch.setattr(common, "utc_today", lambda: date(2026, 12, 31))
        common.begin_request()
        monkeypatch.setattr(common, "utc_today", lambda: date(2027, 1, 1))
        assert common.today() == date(2026, 12, 31)
        assert common.effective_year(None) == 2026
        common.begin_request()
        assert common.today() == date(2027, 1, 1)
    finally:
        common.REQUEST_DATE.reset(token)


@pytest.mark.integration
def test_find_employee_by_name_is_case_insensitive_and_remembers_single_match(seeded, context):
    result = employee_tools.find_employee(context, "henry PARK")
    assert result["total_matches"] == 1
    assert result["data"][0]["emp_id"] == "E1002"
    assert context.state["last_emp_id"] == "E1002"


@pytest.mark.integration
def test_find_employee_ambiguous_or_missing_clears_previous_target(seeded, context):
    context.state["last_emp_id"] = "E1023"
    many = employee_tools.find_employee(context, "a")
    assert many["total_matches"] > 20
    assert len(many["data"]) == 20
    assert context.state["last_emp_id"] is None
    context.state["last_emp_id"] = "E1023"
    assert employee_tools.find_employee(context, "zzzz")["data"] == []
    assert context.state["last_emp_id"] is None


@pytest.mark.integration
@pytest.mark.parametrize("pattern", ["%", "_", "\\"])
def test_find_employee_treats_wildcards_literally(seeded, context, pattern):
    assert employee_tools.find_employee(context, pattern)["total_matches"] == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "actor,name,expected",
    [
        ("E1023", "Yuki Adeyemi", 1),
        ("E1023", "Henry Park", 0),
        ("E1002", "Henry Park", 1),
        ("E1002", "PERSON_1004", 1),
        ("E1002", "PERSON_1050", 0),
        ("E1001", "PERSON_1050", 1),
    ],
)
def test_find_employee_returns_only_accessible_people(
    seeded, context, monkeypatch, actor, name, expected
):
    from company_assistant.db.seed import person_name

    if name.startswith("PERSON_"):
        name = person_name(int(name.removeprefix("PERSON_")))
    monkeypatch.setenv("DEMO_USER_ID", actor)
    result = employee_tools.find_employee(context, name)
    assert result["total_matches"] == expected
    assert len(result["data"]) == expected


@pytest.mark.integration
def test_email_manager_and_direct_reports(seeded, context):
    assert employee_tools.get_employee_email(context, "E1002")["data"] == {
        "emp_id": "E1002",
        "name": "Henry Park",
        "email": "henry.park@example.invalid",
    }
    manager = employee_tools.get_employee_manager(context, "E1023")["data"]
    assert (manager["manager_id"], manager["manager_name"]) == ("E1002", "Henry Park")
    assert employee_tools.get_employee_manager(context, "E1002")["data"]["manager_id"] is None
    reports = employee_tools.list_direct_reports(context, "E1002")
    assert reports["total"] == 37
    assert len(reports["data"]) == 20
    assert reports["truncated"] is True
    everyone = employee_tools.list_direct_reports(context, "E1002", limit=100)
    assert len(everyone["data"]) == 37
    assert everyone["truncated"] is False
    assert employee_tools.list_direct_reports(context, "E1003")["total"] == 20
    assert employee_tools.list_direct_reports(context, "E1023")["data"] == []
    for tool in (
        employee_tools.get_employee_email,
        employee_tools.get_employee_manager,
        employee_tools.list_direct_reports,
    ):
        assert tool(context, "E9999")["code"] == "not_found"


@pytest.mark.integration
def test_list_employees_filters_and_privacy(seeded):
    qa = workforce_tools.list_employees(role="qa", employee_status="Active")
    assert qa["total"] == 10
    assert set(qa["data"][0]) == {
        "emp_id",
        "name",
        "role",
        "department",
        "status",
        "join_date",
        "exit_date",
    }
    resigned = workforce_tools.list_employees(
        employee_status="resigned", exited_start="01-01-26", exited_end="2026-12-31"
    )
    assert resigned["total"] == 5
    assert resigned["filters"]["exited_start"] == "2026-01-01"
    people = workforce_tools.list_employees(department="people")
    assert [row["emp_id"] for row in people["data"]] == ["E1001"]
    unassigned = workforce_tools.list_employees(unassigned=True)
    assert [row["emp_id"] for row in unassigned["data"]] == ["E1049"]


@pytest.mark.integration
def test_list_employees_limit_and_validation(seeded):
    page = workforce_tools.list_employees(limit=3)
    assert (len(page["data"]), page["total"], page["truncated"]) == (3, 60, True)
    for arguments in (
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"employee_status": "boss"},
        {"joined_start": "2026-02-01", "joined_end": "2026-01-01"},
        {"exited_start": "not a date"},
    ):
        assert workforce_tools.list_employees(**arguments)["code"] == "invalid_argument"


@pytest.mark.integration
def test_headcount_breakdowns_and_filters(seeded):
    by_role = workforce_tools.get_headcount(group_by="role")
    assert set(by_role["data"]["breakdown"].values()) == {12}
    assert workforce_tools.get_headcount(department="People")["data"]["total"] == 1
    assert workforce_tools.get_headcount(group_by="salary")["code"] == "invalid_argument"


@pytest.mark.integration
def test_exit_filters_by_role_and_department(seeded):
    assert workforce_tools.get_attrition_stats(2026, role="QA")["data"]["total"] == 2
    assert (
        workforce_tools.get_attrition_stats(2026, department="engineering")["data"]["total"] == 10
    )
    assert workforce_tools.get_layoff_count(2026, department="Engineering")["data"]["count"] == 2
    assert (
        workforce_tools.get_attrition_stats(start_date="2026-01-01")["code"] == "invalid_argument"
    )


@pytest.mark.integration
def test_bench_duration_and_new_hire_status(seeded):
    bench = workforce_tools.list_bench_employees()
    assert bench["total"] == 10
    assert {row["days_on_bench"] for row in bench["data"]} == {60}
    hires = workforce_tools.list_new_hires("2000-01-01", "2030-01-01")
    assert hires["total"] == 80
    assert {"status", "department"} <= set(hires["data"][0])


@pytest.mark.integration
def test_client_and_project_search_filters(seeded):
    assert [c["name"] for c in client_project_tools.list_clients(status="inactive")["data"]] == [
        "Juniper"
    ]
    assert len(client_project_tools.list_clients(industry="technology")["data"]) == 10
    assert client_project_tools.list_clients(status="paused")["code"] == "invalid_argument"
    found = client_project_tools.list_projects(name_contains="project 07")
    assert [p["name"] for p in found["data"]] == ["Delta Project 07"]
    assert client_project_tools.list_projects(name_contains="%")["data"] == []


@pytest.mark.integration
def test_project_team_roster(seeded):
    team = client_project_tools.get_project_team(" delta project 07 ")
    assert team["team_size"] == 3
    assert len(team["data"]) == 3
    assert set(team["data"][0]) == {"emp_id", "name", "role_on_project", "start_date"}
    assert client_project_tools.get_project_team("Juniper Project 19")["data"] == []
    assert client_project_tools.get_project_team("Unknown")["code"] == "not_found"


@pytest.mark.parametrize(
    "spoken,stored",
    [
        ("QA Engineer", "QA"),
        ("qa engineers", "QA"),
        ("Software Engineer", "SDE"),
        ("test-engineer", "SDET"),
        ("DevOps engineers", "DevOps"),
        ("data engineers", "Data Engineer"),
        ("Unknown role", "Unknown role"),
    ],
)
def test_common_role_spellings_map_to_stored_roles(spoken, stored):
    from company_assistant.tools.common import normalize_arguments, role_filter

    assert role_filter(spoken) == stored
    assert normalize_arguments("list_employees", {"role": spoken}, {})["role"] == stored


@pytest.mark.integration
def test_role_spelling_finds_people_and_lists_state_their_total(seeded):
    result = workforce_tools.list_employees(role="QA Engineer", employee_status="active", limit=5)
    assert result["total"] == 10
    assert len(result["data"]) == 5
    assert "only the first 5 are listed" in result["summary"]
    assert "Report 10 as the count" in result["summary"]
    assert list(result)[-1] == "data"
    assert workforce_tools.get_headcount(role="software engineer")["data"]["total"] == 12
    assert "all 10 are listed" in workforce_tools.list_bench_employees()["summary"]


@pytest.mark.integration
def test_lists_default_to_current_employees_unless_asked_otherwise(seeded):
    current = workforce_tools.list_employees(role="QA")
    assert current["total"] == 12
    assert {row["status"] for row in current["data"]} <= {"active", "bench"}
    assert current["filters"]["current_employees_only"] is True
    everyone = workforce_tools.list_employees(role="QA", include_departed=True)
    assert everyone["total"] > current["total"]
    assert "current_employees_only" not in everyone["filters"]
    left = workforce_tools.list_employees(employee_status="retired")
    assert left["total"] == 2
    assert all(row["exit_date"] for row in left["data"])
    exits = [row["exit_date"] for row in left["data"]]
    assert exits == sorted(exits)
    assert workforce_tools.list_employees(include_departed="yes")["code"] == "invalid_argument"


def test_relative_periods_are_exact_on_a_normal_day():
    from datetime import date

    from company_assistant.tools.common import relative_periods

    periods = relative_periods(date(2026, 11, 17))
    assert periods["today"] == {"start_date": "2026-11-17", "end_date": "2026-11-17"}
    assert periods["yesterday"] == {"start_date": "2026-11-16", "end_date": "2026-11-16"}
    assert periods["this week"] == {"start_date": "2026-11-16", "end_date": "2026-11-17"}
    assert periods["last week"] == {"start_date": "2026-11-09", "end_date": "2026-11-15"}
    assert periods["this month"] == {"start_date": "2026-11-01", "end_date": "2026-11-17"}
    assert periods["last month"] == {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    assert periods["last year"] == {"start_date": "2025-01-01", "end_date": "2025-12-31"}
    assert periods["last 7 days"] == {"start_date": "2026-11-11", "end_date": "2026-11-17"}


def test_relative_periods_cross_year_and_leap_boundaries():
    from datetime import date

    from company_assistant.tools.common import relative_periods

    january = relative_periods(date(2027, 1, 1))
    assert january["yesterday"]["start_date"] == "2026-12-31"
    assert january["this week"] == {"start_date": "2026-12-28", "end_date": "2027-01-01"}
    assert january["last month"] == {"start_date": "2026-12-01", "end_date": "2026-12-31"}
    assert january["this year"] == {"start_date": "2027-01-01", "end_date": "2027-01-01"}
    march = relative_periods(date(2028, 3, 1))
    assert march["last month"] == {"start_date": "2028-02-01", "end_date": "2028-02-29"}


@pytest.mark.integration
def test_every_list_tool_shows_twenty_rows_by_default_and_honors_limit(seeded, context):
    hires = ("2000-01-01", "2030-01-01")
    defaults = [
        workforce_tools.list_employees(),
        workforce_tools.list_new_hires(*hires),
        employee_tools.list_direct_reports(context, "E1002"),
    ]
    for result in defaults:
        assert len(result["data"]) == 20
        assert result["total"] > 20
        assert result["truncated"] is True
        assert "only the first 20 are listed" in result["summary"]
    assert len(workforce_tools.list_new_hires(*hires, limit=50)["data"]) == 50
    assert len(workforce_tools.list_new_hires(*hires, limit=100)["data"]) == 80
    assert len(workforce_tools.list_bench_employees(limit=4)["data"]) == 4
    assert len(workforce_tools.list_bench_employees()["data"]) == 10


@pytest.mark.integration
@pytest.mark.parametrize("bad", [0, 101, -1, True, "5", 2.5])
def test_list_tools_reject_bad_limits(seeded, context, bad):
    assert workforce_tools.list_employees(limit=bad)["code"] == "invalid_argument"
    assert workforce_tools.list_bench_employees(limit=bad)["code"] == "invalid_argument"
    assert (
        workforce_tools.list_new_hires("2026-01-01", "2026-12-31", limit=bad)["code"]
        == "invalid_argument"
    )
    assert (
        employee_tools.list_direct_reports(context, "E1002", limit=bad)["code"]
        == "invalid_argument"
    )


@pytest.mark.integration
def test_client_details_for_a_bare_client_name(seeded):
    juniper = client_project_tools.get_client_details(" juniper ")["data"]
    assert (juniper["name"], juniper["status"], juniper["industry"]) == (
        "Juniper",
        "inactive",
        "Technology",
    )
    assert juniper["projects"] == {"total": 2, "active": 0, "on_hold": 0, "completed": 2}
    assert "client_id" not in juniper
    acme = client_project_tools.get_client_details("Acme")["data"]
    assert acme["projects"] == {"total": 2, "active": 2, "on_hold": 0, "completed": 0}
    assert client_project_tools.get_client_details("Nope")["code"] == "not_found"
    assert client_project_tools.get_client_details("")["code"] == "invalid_argument"


@pytest.mark.parametrize(
    "tool,status,expected",
    [
        ("list_clients", "Inactive", "inactive"),
        ("list_clients", "active", "active"),
        ("list_projects", "On_Hold", "on_hold"),
        ("list_projects", "completed", "completed"),
    ],
)
def test_status_is_validated_against_the_tools_own_statuses(tool, status, expected):
    from company_assistant.tools.common import normalize_arguments

    assert normalize_arguments(tool, {"status": status}, {})["status"] == expected


@pytest.mark.parametrize("tool", ["list_clients", "list_projects"])
@pytest.mark.parametrize("word", ["all", "ANY"])
def test_all_or_any_means_no_status_filter(tool, word):
    from company_assistant.tools.common import normalize_arguments

    assert "status" not in normalize_arguments(tool, {"status": word}, {})


@pytest.mark.parametrize(
    "tool,status,message",
    [
        ("list_clients", "completed", "Client status"),
        ("list_clients", "on_hold", "Client status"),
        ("list_projects", "inactive", "Project status"),
        ("list_projects", "bogus", "Project status"),
    ],
)
def test_a_status_from_the_wrong_domain_is_rejected(tool, status, message):
    from company_assistant.tools.common import normalize_arguments

    with pytest.raises(ValueError, match=message):
        normalize_arguments(tool, {"status": status}, {})


@pytest.mark.integration
def test_lists_can_include_each_persons_current_projects_in_one_call(seeded):
    result = workforce_tools.list_employees(role="QA", include_projects=True, limit=100)
    by_id = {row["emp_id"]: row for row in result["data"]}
    assert by_id["E1023"]["current_projects"] == ["Delta Project 07"]
    assert all(isinstance(row["current_projects"], list) for row in result["data"])
    assert result["filters"]["include_projects"] is True
    assert result["total"] == 12


@pytest.mark.integration
def test_current_projects_never_include_history_or_other_people(seeded):
    rows = workforce_tools.list_employees(include_projects=True, limit=100)["data"]
    by_id = {row["emp_id"]: row for row in rows}
    assert "Juniper Project 19" not in by_id["E1023"]["current_projects"]
    ended = {name for row in rows for name in row["current_projects"]}
    assert "Juniper Project 19" not in ended
    plain = workforce_tools.list_employees(limit=3)["data"][0]
    assert "current_projects" not in plain


@pytest.mark.integration
def test_include_projects_handles_empty_results_and_bad_values(seeded):
    empty = workforce_tools.list_employees(role="Unknown role", include_projects=True)
    assert empty["data"] == [] and empty["total"] == 0
    assert workforce_tools.list_employees(include_projects="yes")["code"] == "invalid_argument"
    unassigned = workforce_tools.list_employees(unassigned=True, include_projects=True)
    assert unassigned["data"][0]["current_projects"] == []


@pytest.mark.integration
def test_assigned_lists_only_people_who_currently_have_a_project(seeded):
    everyone = workforce_tools.list_employees(include_projects=True, limit=100)["data"]
    on_a_project = {row["emp_id"] for row in everyone if row["current_projects"]}
    result = workforce_tools.list_employees(assigned=True, include_projects=True, limit=100)
    assert result["total"] == len(on_a_project) > 0
    assert {row["emp_id"] for row in result["data"]} == on_a_project
    assert all(row["current_projects"] for row in result["data"])
    assert result["filters"]["assigned"] is True


@pytest.mark.integration
def test_assigned_combines_with_role_limit_and_join_date(seeded):
    result = workforce_tools.list_employees(
        role="QA Engineer", assigned=True, include_projects=True, limit=2
    )
    assert result["total"] >= 1
    assert len(result["data"]) <= 2
    assert all(row["role"] == "QA" and row["current_projects"] for row in result["data"])
    assert all(row["join_date"] for row in result["data"])


@pytest.mark.integration
def test_the_two_project_filters_never_overlap_and_assigned_false_means_unassigned(seeded):
    with_project = workforce_tools.list_employees(assigned=True, limit=100)["data"]
    without = workforce_tools.list_employees(unassigned=True, limit=100)["data"]
    assert not {row["emp_id"] for row in with_project} & {row["emp_id"] for row in without}
    as_false = workforce_tools.list_employees(assigned=False, limit=100)
    assert [row["emp_id"] for row in as_false["data"]] == [row["emp_id"] for row in without]
    assert as_false["filters"]["unassigned"] is True


@pytest.mark.integration
def test_assigned_rejects_conflicts_and_bad_values(seeded):
    both = workforce_tools.list_employees(assigned=True, unassigned=True)
    assert both["code"] == "invalid_argument"
    assert "not both" in both["message"]
    assert workforce_tools.list_employees(assigned="yes")["code"] == "invalid_argument"
    assert workforce_tools.list_employees(assigned=None)["total"] == 60


@pytest.mark.integration
def test_a_list_can_be_limited_to_one_project_and_agrees_with_the_team_roster(seeded):
    roster = client_project_tools.get_project_team("Delta Project 07")["data"]
    result = workforce_tools.list_employees(project_name=" delta project 07 ", limit=100)
    assert {row["emp_id"] for row in result["data"]} == {m["emp_id"] for m in roster}
    assert result["total"] == 3
    assert result["filters"]["project_name"] == "delta project 07"
    only_qa = workforce_tools.list_employees(project_name="Delta Project 07", role="QA")
    assert [row["emp_id"] for row in only_qa["data"]] == ["E1023"]


@pytest.mark.integration
def test_a_list_can_be_limited_to_one_clients_projects(seeded):
    expected = {
        member["emp_id"]
        for project in ("Delta Project 07", "Delta Project 08")
        for member in client_project_tools.get_project_team(project)["data"]
    }
    result = workforce_tools.list_employees(client_name="delta", limit=100)
    assert {row["emp_id"] for row in result["data"]} == expected
    assert result["total"] == len(expected) > 3
    assert workforce_tools.list_employees(client_name="Juniper")["total"] == 0


@pytest.mark.integration
def test_unknown_projects_and_clients_are_not_found_not_silently_empty(seeded):
    assert workforce_tools.list_employees(project_name="Nope")["code"] == "not_found"
    assert workforce_tools.list_employees(client_name="Nope")["code"] == "not_found"
    assert workforce_tools.list_employees(project_name="  ")["code"] == "invalid_argument"


@pytest.mark.integration
def test_lists_can_be_ordered_by_join_date_in_either_direction(seeded):
    base = workforce_tools.list_employees(limit=100)["data"]
    oldest = workforce_tools.list_employees(order_by="join_date", limit=100)["data"]
    newest = workforce_tools.list_employees(order_by="Join Date", descending=True, limit=100)
    assert [r["join_date"] for r in oldest] == sorted(r["join_date"] for r in base)
    assert [r["join_date"] for r in newest["data"]] == sorted(
        (r["join_date"] for r in base), reverse=True
    )
    assert newest["filters"]["order_by"] == "join_date"
    assert newest["filters"]["descending"] is True
    top_ten = workforce_tools.list_employees(order_by="join_date", limit=10)
    assert [r["emp_id"] for r in top_ten["data"]] == [r["emp_id"] for r in oldest[:10]]


@pytest.mark.integration
def test_exit_date_ordering_puts_people_without_one_last(seeded):
    rows = workforce_tools.list_employees(
        include_departed=True, order_by="exit_date", descending=True, limit=100
    )["data"]
    exits = [r["exit_date"] for r in rows]
    assert exits[0] is not None
    first_missing = exits.index(None)
    assert all(value is None for value in exits[first_missing:])
    assert exits[:first_missing] == sorted(exits[:first_missing], reverse=True)


@pytest.mark.integration
@pytest.mark.parametrize(
    "arguments",
    [{"order_by": "salary"}, {"order_by": ""}, {"order_by": 5}, {"descending": "yes"}],
)
def test_bad_ordering_arguments_are_rejected(seeded, arguments):
    assert workforce_tools.list_employees(**arguments)["code"] == "invalid_argument"
