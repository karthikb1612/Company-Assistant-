PRAGMA foreign_keys = ON;

CREATE TABLE employees (
    emp_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    department TEXT NOT NULL,
    prior_experience_years REAL NOT NULL CHECK (prior_experience_years >= 0),
    join_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'bench', 'resigned', 'laid_off', 'terminated', 'retired')),
    bench_since TEXT,
    exit_date TEXT,
    exit_reason TEXT,
    manager_id TEXT REFERENCES employees(emp_id),
    app_role TEXT NOT NULL CHECK (app_role IN ('employee', 'manager', 'hr')),
    CHECK (manager_id IS NULL OR manager_id <> emp_id),
    CHECK ((status = 'bench' AND bench_since IS NOT NULL AND bench_since >= join_date)
        OR (status <> 'bench' AND bench_since IS NULL)),
    CHECK ((status IN ('active', 'bench') AND exit_date IS NULL)
        OR (status NOT IN ('active', 'bench') AND exit_date IS NOT NULL AND exit_date >= join_date))
);

CREATE TABLE clients (
    client_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    industry TEXT NOT NULL,
    start_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE projects (
    project_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    client_id INTEGER NOT NULL REFERENCES clients(client_id),
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'on_hold')),
    start_date TEXT NOT NULL,
    end_date TEXT,
    CHECK (end_date IS NULL OR end_date >= start_date),
    CHECK ((status = 'completed' AND end_date IS NOT NULL)
        OR (status <> 'completed' AND end_date IS NULL))
);

CREATE TABLE assignments (
    assignment_id INTEGER PRIMARY KEY,
    emp_id TEXT NOT NULL REFERENCES employees(emp_id),
    project_id INTEGER NOT NULL REFERENCES projects(project_id),
    start_date TEXT NOT NULL,
    end_date TEXT,
    role_on_project TEXT NOT NULL,
    CHECK (end_date IS NULL OR end_date >= start_date),
    UNIQUE (emp_id, project_id, start_date)
);

CREATE TABLE dataset_metadata (as_of TEXT NOT NULL);
CREATE INDEX employees_status_exit ON employees(status, exit_date);
CREATE INDEX employees_manager ON employees(manager_id);
CREATE INDEX projects_client ON projects(client_id);
CREATE INDEX assignments_employee ON assignments(emp_id, project_id);
CREATE INDEX assignments_project ON assignments(project_id, end_date);