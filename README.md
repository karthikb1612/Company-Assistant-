# Company Assistant

A local, **read-only** chat assistant for **synthetic** workforce, client, project, and employee data. You ask questions in plain English ("Who is on Delta Project 07?", "How many QA engineers were laid off in 2025?", "What is my email address?") and it answers from a SQLite database through a small set of fixed tools.

It is built with [Google ADK](https://google.github.io/adk-docs/) (agents, tools, sessions, the dev UI) and Cohere (the language model, through LiteLLM). The interface is ADK's built-in development UI, not a custom frontend.

> **Use synthetic data only and keep this server local.** ADK Web endpoints are unauthenticated. Questions and authorized tool results are sent to Cohere. The role checks are a demo policy, not production authentication.

## Contents

- [What it can do](#what-it-can-do)
- [How it works](#how-it-works)
- [Quickstart](#quickstart-windows-powershell)
- [Configuration](#configuration)
- [Roles and permissions](#roles-and-permissions)
- [Example questions](#example-questions)
- [Answer format](#answer-format)
- [Safety design](#safety-design)
- [Data](#data)
- [Project layout](#project-layout)
- [Testing](#testing)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)

## What it can do

| Area | Examples |
| --- | --- |
| Headcount and bench | Total, active, bench; by role or department; bench names and how long each person has been benched |
| Hiring and exits | New hires in a date window; layoffs, resignations, terminations, retirements by year or window, optionally by role or department |
| Employee lists | By role, status, department, join or exit date; people with or without a project; people on a given project or client; with their current project names; ordered by join or exit date |
| One employee | Find by name or ID; profile, email, manager, direct reports, project history; "my" and "me" resolve to the signed-in user |
| Clients and projects | Client counts and details, project lists by client, status, name, or date window, project details, current project team |

It does **not** answer questions about salaries, performance, policies, or other companies, and it cannot write, export, or run code. Rates (such as an attrition percentage) and historical headcount are not available.

## How it works

Every question goes through the same pipeline. The principle is **the model proposes, code decides**: language models understand the question and choose steps, but identity, access, dates, delegation, and limits are enforced by deterministic code.

```text
You ──► ADK Web
          │
   1. Trusted identity      the signed-in user comes from DEMO_USER_ID, never from chat
   2. Scope check           is this a company-data question? (model classifier, fails closed)
   3. Planner               writes a JSON plan: up to 6 steps, each for one specialist
   4. Coordinator           delegates each planned step, one at a time
          │
          ├─► workforce_agent          7 tools   headcount, bench, exits, employee lists
          ├─► client_project_agent     6 tools   clients, projects, project teams
          └─► employee_profile_agent   6 tools   find, profile, email, manager, reports
                   │
   5. Per tool call: normalize arguments ► authorize ► audit ► run read-only SQL
   6. Scope check on each specialist step and on the final answer
          │
   7. Final answer in a fixed, answer-first format ──► You
```

What each layer does, and where it lives:

| Layer | What it does | Code |
| --- | --- | --- |
| Agents and skills | One coordinator and three specialists. Each loads a fixed `SKILL.md` (scope, workflow, boundaries, examples) as its instructions | [agent.py](company_assistant/agent.py), [sub_agents/](company_assistant/sub_agents), [skills/](company_assistant/skills) |
| Planner | Validates a strict plan, merges independent steps for the same specialist into one numbered request, resolves "today / last week" to exact dates in code, knows the signed-in user and the client names, and sees a capability table generated from the real tool list | [planning.py](company_assistant/planning.py) |
| Deterministic delegation | The specialist receives the validated plan task, not the coordinator's rewording. Out-of-order, duplicate, and stale calls are blocked, and a text reply before delegation is replaced by the plan's next step | [planning.py](company_assistant/planning.py) |
| Tools | 19 fixed, parameterized, read-only SQL tools. No generated SQL, no code execution | [tools/](company_assistant/tools) |
| Authorization and audit | Default-deny policy per tool, checked against the real employee record. Every attempt is written to a separate audit database | [security/permissions.py](company_assistant/security/permissions.py), [security/callbacks.py](company_assistant/security/callbacks.py), [security/audit.py](company_assistant/security/audit.py) |
| Scope guard | A tool-free model call reviews each request, each specialist step, and each answer | [security/topic_guard.py](company_assistant/security/topic_guard.py) |
| Model layer | One wrapper for every model call: retries transient errors with backoff, optional pacing, accurate messages for rate and monthly limits | [model_resilience.py](company_assistant/model_resilience.py) |

Model calls per question: a simple question uses roughly 8 to 12 calls (two scope checks, the planner, coordinator and specialist turns, and answer reviews). A multi-part question can use 30 or more. This matters on a Cohere Trial key; see [Configuration](#configuration).

The [architecture notes](docs/architecture.md) give the full contracts, error codes, and limitations.

## Quickstart (Windows PowerShell)

Run these commands from the repository root. You need Python 3.12 and internet access to install packages. No Google API key or Google Cloud project is needed.

1. Create the environment:

   ```powershell
   py -3.12 -m venv .venv
   ```

2. Install dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

3. Configure [company_assistant/.env](company_assistant/.env): set `COHERE_API_KEY` to your Cohere key and keep `DEMO_USER_ID=E1023` for the employee demo. The app loads this file automatically.

   Leave the key blank for offline tests. Never paste it into chat, screenshots, or committed code. The file is Git-ignored, but OneDrive may still sync it. Restart the server after changing it. Existing process environment variables take precedence over the file.

4. Generate the synthetic data (no key needed):

   ```powershell
   .\.venv\Scripts\python.exe -m company_assistant.db.seed
   ```

   Use `--as-of 2026-11-16` for repeatable evaluation. Existing data is never overwritten unless you add `--reset`, which replaces only company data and keeps audit history.

5. Run the offline tests:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest --cov=company_assistant --cov-branch --cov-report=term-missing
   ```

6. Start the UI:

   ```powershell
   .\.venv\Scripts\adk.exe web . --host 127.0.0.1 --port 8000 --no_use_local_storage --max_llm_calls 12 --no-reload
   ```

   Open http://127.0.0.1:8000 and choose `company_assistant`. Stop with Ctrl+C. Without a key, requests return a local configuration message instead of contacting Cohere.

7. Try another role: stop the server, change `DEMO_USER_ID` in the `.env` file, restart, and start a new session. Remove any terminal override of that variable first. Never change identity by asking the chatbot.

On Linux or macOS use `python3.12`, `.venv/bin/python`, `.venv/bin/adk`, and your shell's syntax for environment variables.

## Configuration

Set these in [company_assistant/.env](company_assistant/.env) (the secret-free template is [.env.example](company_assistant/.env.example)).

| Variable | Default and purpose |
| --- | --- |
| `COHERE_API_KEY` | Required for live chat; not needed for seeding or offline tests |
| `COHERE_MODEL` | `cohere_chat/command-a-03-2025` |
| `DEMO_USER_ID` | Required. The signed-in employee; there is no implicit privileged user |
| `MODEL_CALLS_PER_MINUTE` | `0` (off). On a Cohere **Trial** key set `18`, so the app paces itself under the 20-calls-a-minute limit. A Production key needs no setting |
| `COMPANY_DB_PATH` | `company_assistant/db/company.db` |
| `AUDIT_DB_PATH` | `company_assistant/db/audit.db` (must differ from the company database) |
| `RUN_LIVE_TESTS` | Must be exactly `1` to run the paid smoke test |

Both databases default to the package's `db/` directory regardless of the working directory, and they are Git-ignored along with their SQLite sidecar files. Git ignore rules do **not** stop OneDrive from syncing them. Conversation sessions live in memory, so a restart clears them while the audit log persists.

## Roles and permissions

Demo identities (with the full seed, set `DEMO_USER_ID` to one of these):

| ID | Name | Role |
| --- | --- | --- |
| `E1001` | Natalie Park | HR |
| `E1002` | Henry Park | Manager (E1023 reports to this person) |
| `E1023` | Yuki Adeyemi | Employee |

| Capability | Employee | Manager | HR |
| --- | --- | --- | --- |
| Headcount, bench totals, clients, project metadata | Yes | Yes | Yes |
| Exit and layoff counts, bench names, new hires, employee lists, project team members | No | Yes | Yes |
| Own profile, email, manager, project history | Yes | Yes | Yes |
| Another person's profile, email, manager, project history | No | Direct reports only | Yes |
| Direct reports of a manager | No | Own reports only | Yes |
| Find a person by name | Self only | Self and direct reports | Everyone |

A name search returns only people the signed-in user may access, and an unauthorized name looks identical to a name that does not exist. A role claimed in chat ("I am HR") never changes access.

## Example questions

- **Company:** "How many employees are on the bench?", "How many people left in 2025 and 2026, by reason?", "Who has been on the bench the longest?"
- **Lists:** "List all QA engineers", "Give 10 data engineers with project names (only those on a project) and join date", "Who joined most recently?"
- **People:** "What is the email id for Henry Park?", "Who reports to Isha Choi?", "Who does Yuki Adeyemi report to?"
- **Yourself:** "What is my email address?", "Who do I report to?"
- **Clients and projects:** "What projects does Acme have?", "Tell me about Juniper", "Who is on Delta Project 07?"
- **Dates:** "How many people were hired last week?", "How many SDEs were laid off between 06-01-26 and 11-17-26?" Dates can be `MM-DD-YY` or `YYYY-MM-DD`. Today, yesterday, this or last week, this or last month, this or last year, and the last 7, 30, or 90 days are computed in code. A window needs both ends.
- **Follow-ups:** after "Who is E1023?" ask "What projects have they worked on?"

If a question is incomplete ("How many were hired recently?"), the assistant asks for the missing detail and combines your reply with the original question. Lists show 20 rows by default; ask for more (up to 100) or add a filter. Headcount includes bench employees, an omitted year means the current UTC year, and experience stops at exit for people who left.

More: [tests/eval_questions.md](tests/eval_questions.md) lists expected outcomes for every role.

## Answer format

Every data answer uses the same answer-first layout:

1. **Answer:** one or two plain sentences with the key figure or name in bold.
2. **Details:** a compact table only when there is a list, breakdown, or record (a Field | Value table for one person or project).
3. **Note:** a quoted line only when needed, such as a partial list, a denied portion, or an applied default like "current employees only".
4. **In short:** one sentence, only when the answer covers two or more separate topics.
5. **Footer:** one italic line with the as-of date and the filters applied, for example *As of Nov 17, 2026 · Job role: QA · Current employees only*. Unknown parts are left out; it never shows "N/A".

Dates read "Nov 17, 2026", numbers use thousands separators, and tables use a fixed column order (Employee ID, Name, Role, Department, Status, then dates). Long lists say "Showing N of TOTAL". Every agent is also told to apply each constraint you state and to say so when one cannot be applied, so results are never presented as if they matched a filter that was ignored. Greetings, clarification questions, refusals, and errors stay brief.

The format is an instruction in the [coordinator skill](company_assistant/skills/company-assistant/SKILL.md), which also holds worked examples. It is not enforced by a parser, so small slips are possible. Restart ADK after changing a skill.

## Safety design

- **Read-only data path:** fixed Python tools with parameterized SQL over read-only connections. The model never writes SQL. Only the seed command writes company data.
- **Identity:** the signed-in user is read from `DEMO_USER_ID` and verified against the database on every tool call. Session values and chat text are informational only.
- **Authorization:** default-deny, per tool and per target. Denials do not reveal whether a record exists.
- **Audit:** every tool attempt (allowed or denied) is logged with time, user, tool, allowlisted arguments, and outcome. Name searches log only the length of the name. If the audit write fails, the tool does not run.
- **Scope guard:** a separate tool-free model call reviews each request, each specialist step, and each answer, and fails closed on any error or malformed decision. It is given the company's client names (so a client called "Delta" is not mistaken for an outsider) and, for specialist steps, the question the coordinator already approved. Standalone greetings are answered locally.
- **Local checks:** oversized or non-text input, attachments, and any text containing the configured API key are rejected without a model call. No unreviewed streaming text reaches the UI.
- **Honest limits:** the scope guard and planner are models. They can misjudge, and they are not guaranteed prompt-injection protection or a factual-accuracy check. The tool allowlist, role checks, parameterized SQL, and read-only connections are the data-access boundary, not the models.

## Data

The base seed has 80 fictional employees, 10 clients, 20 projects, and their assignments. To add 2,000 more employees without deleting anything (stop ADK and back up both databases first):

```powershell
.\.venv\Scripts\python.exe -m company_assistant.db.seed --append-count 2000 --as-of 2026-11-16
```

Each run adds a new batch with the next free four-digit IDs. New employees get unique, realistic-looking fictional names (derived from the employee number), matching `example.invalid` email addresses, a mix of statuses, and the employee role. Existing managers are reused; clients and projects are unchanged, and new employees start with no project history. The command also writes marked synthetic audit rows (`dummy_allowed` / `dummy_denied`, correlation prefix `dummy-seed:`) in one transaction that rolls back both databases on failure. Do not combine `--append-count` with `--reset`.

Numeric expectations in the evaluation checklist apply to the base seed only; after expansion, answers change. Keep backups under `company_assistant/db/backups/`.

## Project layout

```text
company_assistant/
  agent.py              root agent and callback wiring
  planning.py           planner, plan validation, delegation control
  model_resilience.py   retries, pacing, limit messages for every model call
  config.py             settings and .env loading
  sub_agents/           the three specialists and shared instructions
  skills/               one SKILL.md per agent (instructions)
  tools/                read-only SQL tools (workforce, client/project, employee)
  security/             permissions, authorization callbacks, audit, scope guard
  db/                   schema, seed command, read-only connections
docs/architecture.md    contracts, error codes, data semantics
tests/                  offline tests and the evaluation checklist
```

## Testing

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest --cov=company_assistant --cov-branch --cov-report=xml
.\.venv\Scripts\python.exe -m pip_audit --progress-spinner off
```

Offline tests block network access. They use temporary SQLite files and scripted model responses through real ADK runners, and cover tool results, every permission combination, audit behavior, planning and delegation rules, scope-guard handling, the answer-format contract, and the model-resilience layer. Coverage must be at least 80%, and CI also checks per-file and changed-line coverage on Windows and Linux and runs a separate dependency audit. Nothing is deployed.

Scripted tests cannot show how Cohere actually routes, plans, or classifies. For that:

- The paid smoke test (needs a key) checks a headcount round trip and its audit record:

  ```powershell
  $env:RUN_LIVE_TESTS = "1"
  .\.venv\Scripts\python.exe -m pytest -m live tests/test_agents.py
  Remove-Item Env:RUN_LIVE_TESTS
  ```

- Full model-quality acceptance is the manual checklist in [tests/eval_questions.md](tests/eval_questions.md), run per role. Selected live checks were made during development, but the whole checklist has not been completed.

## Known limitations

- **Model-dependent behavior:** planning, scope classification, tool-argument choice, and answer formatting come from the model. Expect occasional wrong plans, false refusals, or formatting slips; the troubleshooting log lines below help pinpoint them.
- **Trial-key limits:** a Cohere Trial key allows 20 calls per minute and 1,000 per month, which a multi-part question can use up quickly.
- **Dependency audit blocker:** the audit reports vulnerabilities in `filelock` 3.32.7. Fixes need 4.2.1 or later, but the pinned LiteLLM requires `<4`. Do not suppress the audit or force an incompatible upgrade; a compatible upstream change and a fresh audit are needed before release.
- **Deprecation warning:** explicit `AgentTool` works in the pinned ADK, but newer guidance discourages it and its nested runner warns about plugin arguments. The warning is not suppressed.
- **Scope:** one employment period per person, no salaries, no editing, no exports, no historical headcount, no rates, and answers reflect the database as of the current date.

## Troubleshooting

Most failures print a sanitized line in the terminal running `adk web`. Share that line, never your key.

| What you see | Meaning and what to do |
| --- | --- |
| "Set COHERE_API_KEY…" or a configuration message | No key is loaded. Set it in the `.env` file and restart. Process variables override the file; clear stale ones with `Remove-Item Env:COHERE_API_KEY, Env:DEMO_USER_ID -ErrorAction SilentlyContinue` |
| Missing or invalid identity or database | Seed first and set `DEMO_USER_ID` to a current employee ID |
| "The model service is rate limited right now" | Cohere returned HTTP 429. Calls are already retried with backoff. On a Trial key set `MODEL_CALLS_PER_MINUTE=18` and avoid running other tests on the same key at the same time |
| "The model provider's monthly usage limit for this API key has been reached" | The Trial key's 1,000 calls a month are used. Waiting does not help and nothing is retried. Use a Production key or wait for the reset |
| "The model service is unavailable" with a status code | Authentication (401), access (403), model or endpoint (404), bad request (400/422), or a timeout (408/504). The terminal shows `Model request failed: error_type=… status_code=…`. Restart after any change and retry once |
| "I can only help with supported company…" | The scope guard refused the question as out of scope. Ask for supported company data, one topic at a time. If a question that looks valid is refused, rephrase with the employee, client, or project name and report the wording |
| "One part of your request was blocked by a scope check" | A specialist's check rejected one step of a multi-part question. Ask that part on its own. The log shows `Topic guard blocked: agent=<name> reason=out_of_scope_input` (the task) or `out_of_scope_output` (the answer) |
| "The topic safety check is unavailable" | The scope reviewer failed or returned an invalid decision. Nothing unchecked is released. Retry; the terminal shows the error type |
| "I could not create a valid company-data plan" | The planning call failed or its reply was not a valid plan (it gets one repair attempt). The log line `Request planning failed … detail=…` gives a value-free reason such as `invalid plan (steps.0.agent: literal_error)` |
| "I could not finish all planned steps" | The coordinator could not complete the plan even after a recovery attempt. Narrow the question. The log line `Coordinator reply discarded and plan left incomplete` confirms it |
| "No accessible match" or a permission message | Expected when the signed-in role may not see that person or data. Switch roles by changing `DEMO_USER_ID` and restarting, not by asking the chatbot. Do not weaken the permission matrix to work around a model-generated refusal |
| Data or audit unavailable | Check both database paths and that the audit directory is writable. An audit failure blocks every tool call |
| `No module named 'mcp'` | The pinned LiteLLM needs the MCP SDK on its completion path. Reinstall the requirements in the active virtual environment and restart. No MCP tools are exposed |
| SQLite locks or odd file errors | OneDrive may be syncing the databases in `db/`. Pause syncing; never move or reset databases while the server is running |

The plan and its progress are visible in the ADK session state: `request_plan`, `plan_status` (awaiting_scope, planning, ready, executing, needs_clarification, completed, failed), `plan_next_step`, and `plan_pending`. "Completed" means the planned steps returned, not that every request was authorized or successful.

See [CHANGELOG.md](CHANGELOG.md) for implemented scope and verification limits.
