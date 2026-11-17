# Company Assistant

A local, read-only chat demo for **synthetic** workforce, client, project, and employee data. Google ADK coordinates three specialists through `AgentTool`; Cohere supplies the model through LiteLLM. The interface is ADK's built-in development UI, not a custom frontend.

**Do not use real company data or expose this server to other users.** ADK Web endpoints are unauthenticated. Model requests and authorized tool results leave your machine for Cohere. Role checks are a demo policy, not production authentication.

## Quickstart (Windows PowerShell)

Run these commands from this repository's root. Python 3.12 and internet access for package installation are required. No Google API key or Google Cloud project is needed.

1. Create the environment:

   ```powershell
   py -3.12 -m venv .venv
   ```

2. Install dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

3. Configure [company_assistant/.env](company_assistant/.env): enter your Cohere key as the value of `COHERE_API_KEY`. Keep `DEMO_USER_ID=E1023` for the employee demo. The app loads this file automatically; terminal key entry is not required.

   Leave the key blank for offline tests. Never paste it into chat, screenshots, or committed source. This file is Git-ignored, but OneDrive may still sync it. Restart the server after changing its values. Existing process environment variables take precedence over this file.

4. Generate the synthetic data (no key required):

   ```powershell
   .\.venv\Scripts\python.exe -m company_assistant.db.seed
   ```

   For repeatable evaluation use `--as-of 2026-11-16`. Existing data is not overwritten unless you explicitly add `--reset`. Reset replaces only synthetic company data and leaves audit history intact.

5. Run offline tests:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest --cov=company_assistant --cov-branch --cov-report=term-missing
   ```

6. Launch the local UI:

   ```powershell
   .\.venv\Scripts\adk.exe web . --host 127.0.0.1 --port 8000 --no_use_local_storage --max_llm_calls 12 --no-reload
   ```

   Open http://127.0.0.1:8000 and select `company_assistant`. Stop the server with Ctrl+C. If port 8000 is occupied, choose another port. Without a key, requests return a local configuration message instead of contacting Cohere.

7. Test a different role: stop the server, change `DEMO_USER_ID` in [company_assistant/.env](company_assistant/.env), restart, and start a fresh session. Remove any older terminal override for that variable first. E1023 is an employee; E1002 manages E1023 but not E1050; E1001 is HR. Never change identity by asking the chatbot.

On Linux/macOS, use `python3.12`, `.venv/bin/python`, `.venv/bin/adk`, and your shell's environment-variable syntax.

## Configuration

| Variable | Default / purpose |
| --- | --- |
| `COHERE_API_KEY` | Required for live chat, not seeding or offline tests |
| `COHERE_MODEL` | `cohere_chat/command-a-03-2025` |
| `DEMO_USER_ID` | Required; no implicit privileged user |
| `COMPANY_DB_PATH` | `company_assistant/db/company.db` inside the project |
| `AUDIT_DB_PATH` | `company_assistant/db/audit.db` inside the project |
| `RUN_LIVE_TESTS` | Must explicitly be `1` for paid smoke tests |

On all systems, both databases default to the package's `company_assistant/db/` directory, resolved independently of the working directory. Database paths must differ; explicit path environment variables still override the defaults. Generated databases and their SQLite sidecar files are Git-ignored. Store local credentials and configuration in [company_assistant/.env](company_assistant/.env); [company_assistant/.env.example](company_assistant/.env.example) is the secret-free template. Loading never overrides existing process environment variables. Git ignore rules do **not** prevent OneDrive from syncing the databases or local credentials file.

For an existing installation using the previous application-data location, stop the server before relocating both databases and restart afterward. Preserve existing audit history; do not reseed or overwrite an existing destination as a migration shortcut.

Conversation sessions are in memory with the documented launch command. Restarting clears them. Audit records persist separately. The base seed contains 80 fictional employees, 10 clients, and 20 projects; it can be expanded explicitly with the append command below.

## Additional Dummy Data

Stop ADK and back up both databases before expanding an existing demo. To append 2,000 employees and 2,000 audit rows without deleting existing data:

```powershell
.\.venv\Scripts\python.exe -m company_assistant.db.seed --append-count 2000 --as-of 2026-11-16
```

Each run adds a new batch, allocating employee IDs after the highest existing four-digit ID. Insufficient ID capacity fails without inserting a partial batch. New employees get unique, realistic-looking fictional names (derived deterministically from the employee number), matching `example.invalid` emails, a mix of statuses, and the unprivileged employee role. Existing managers are reused. Clients, projects, and assignments are unchanged; new employees initially have no project history.

Synthetic audit rows use `dummy_allowed` or `dummy_denied` reasons and a `dummy-seed:` correlation prefix, so they cannot be mistaken for real tool-access attempts. Both inserts use a seed-only SQLite transaction; an insertion failure rolls back the batch in both databases. Do not combine `--append-count` with `--reset`. Workforce answers change after expansion; baseline evaluation counts apply only to the base seed. Keep any database backups under `company_assistant/db/backups/`; generated database files remain Git-ignored.

## Questions And Permissions

- Everyone: headcount (also by role or department), grouped bench counts, clients, and project metadata.
- Managers and HR: layoff/exit aggregates (also by role or department), bench names/IDs and bench duration, new hires, employee lists by role/status/department/dates, employees with no project, and project team members.
- Employee lookups work by name or ID: profile, email address, manager, direct reports, and project history. Employees see themselves; managers see themselves and direct reports; HR sees all synthetic employees. A name search returns only people the signed-in user may access.
- Headcount includes bench employees. Omitted years mean the current UTC year. Project counts use distinct projects, not assignment rows. Experience stops at exit for departed employees.

You can also say "my", "me" or "I" about your own record, for example "What is my email address?" or "Who do I report to?"; the assistant uses the verified signed-in employee. Try "How many employees are on the bench?", "What projects does Acme have?", or "Who is E1023?" Follow with "What projects have they worked on?" Also try "What is the email id for Henry Park?", "Who reports to Isha Choi?", "List all QA engineers", "Who is on Delta Project 07?", or "How many people were hired last week?" If the question is incomplete, such as "How many were hired recently?", the assistant asks for the missing detail and combines your reply with the original question. A fresh session or an ambiguous multi-person lookup requires an explicit ID or a more specific name.

The [evaluation checklist](tests/eval_questions.md) includes expected outcomes for all roles. The [architecture notes](docs/architecture.md) define tools, errors, permissions, and limitations.

## Answer Format

The coordinator skill gives every data answer the same answer-first layout:

1. **Answer:** one or two plain sentences with the key figure or name in bold. No "Result" heading.
2. **Details:** a compact table only when there is a list, breakdown, or record (a Field | Value table for one person or project).
3. **Note:** a quoted line only when needed, for a partial list, a denied portion, an applied default such as "current employees only", or an offered filter.
4. **In short:** one sentence, only when the answer covers two or more separate topics.
5. **Footer:** one italic line with the as-of date and filters, for example *As of Nov 17, 2026 · Role: QA · Current employees only*. Unknown parts are left out; it never shows "N/A".

Dates read Nov 17, 2026, numbers use thousands separators, tables use a fixed column order (Employee ID, Name, Role, Department, Status, then dates), and long lists show 20 rows by default with "Showing N of TOTAL" (the tools return 20 unless the user asks to see more, up to 100). Greetings, clarification questions, refusals, and service errors stay brief.

This is an instruction-level format in the [coordinator skill](company_assistant/skills/company-assistant/SKILL.md), which also contains worked examples. It is not an output-rewriting model or a hard Markdown validator, adds no provider calls, and does not change planning, permissions, or topic checks. Offline tests verify the contract is injected and formatted results survive the real ADK pipeline; live adherence still varies by model run. Restart ADK after changing the skill.

## Runtime Skills And Guardrails

Each agent loads a dedicated ADK-validated skill at startup:

| Agent | Skill |
| --- | --- |
| Coordinator | [Company Assistant](company_assistant/skills/company-assistant/SKILL.md) |
| Workforce | [Workforce](company_assistant/skills/workforce/SKILL.md) |
| Clients/projects | [Client And Project](company_assistant/skills/client-project/SKILL.md) |
| Employee profiles | [Employee Profile](company_assistant/skills/employee-profile/SKILL.md) |

These are application runtime skills, not VS Code/Copilot instructions. Each defines scope, tool-selection workflow, permission rules, response conventions, and refusal examples. The native ADK loader validates frontmatter; only fixed agent-to-directory mappings are accepted. Missing or invalid skill files prevent agent construction. Restart ADK after changing a skill. No filesystem-reading, shell, or extra skill-discovery tools are exposed to the model.

The [topic guard](company_assistant/security/topic_guard.py) checks each incoming request against the receiving agent's domain before its model/tools execute, and reviews completed answers before release. It uses a separate, tool-free classification request to the configured model, not a keyword-only filter. Requests mixing supported company questions with unrelated tasks are refused as a whole. Unsupported topics include weather, recipes, general coding/advice, salaries, policies, and data modification. A claimed role in chat never changes database permissions.

Brief standalone greetings/thanks receive fixed replies without model calls. Oversized/non-text input, mixed attachments, and an exact occurrence of the configured API key are rejected locally. The UI receives no unreviewed streaming text or commentary accompanying tool calls. Reviewer failures, timeouts, malformed JSON, or non-boolean decisions fail closed. Denied/failed requests clear stale employee/topic references. Logs contain only agent name/reason or exception class, not the rejected text or credential values.

**Cost and limits:** each participating agent normally makes two additional small model calls, one input review and one final-answer review. A normal root-plus-specialist turn therefore typically adds four calls. Reviews have a 15-second deadline, a 64-token output limit, and use the same per-agent call budget as other model requests. They receive the current question, bounded prior-topic context, and candidate answer when reviewing output; this data is sent to Cohere. Only an exact configured-key match is blocked locally, not arbitrary sensitive data.

**Verification limit:** semantic classification is model-dependent, can reject valid questions, and is not guaranteed prompt-injection protection or a factual-accuracy check. Offline tests verify enforcement using scripted decisions, including actual ADK streaming; they do not prove live Cohere classification. The deterministic tool allowlist, role checks, parameterized SQL, and read-only connections remain the data-access boundary. Complete the live topic checklist before claiming production-grade behavior.

## Plan Before Execution

Every accepted company question now follows this flow:

1. Verify identity and check topic scope.
2. Create and validate a fresh structured plan before any specialist is called.
3. Ask a fixed clarification question if an essential reference is missing.
4. Otherwise execute planned specialists sequentially, preserving existing tool permissions and audit checks.
5. Review and return the final answer after every planned delegation has returned.

The [request planner](company_assistant/planning.py) uses a separate tool-free model call. Plans contain up to six steps, each with a registered specialist, short task description, and dependencies on earlier steps. They contain tasks, not hidden reasoning or invented results. Execution proceeds automatically after validation; there is no additional human approval step. Standalone greetings and rejected topics do not invoke planning.

Inspect `request_plan` in the ADK session state to see the plan. `plan_status` records awaiting_scope, planning, ready, executing, needs_clarification, completed, or failed. `plan_next_step` counts the delegations already returned; `plan_pending` indicates one is in progress. Completed does not mean every request was authorized or successful. Existing audit `allowed` values represent role authorization, not completion of a plan step.

Out-of-order, duplicate in-flight, unplanned, and stale-invocation calls are blocked. A missing/malformed plan, invalid dependency, planner error, or timeout prevents execution. An early final answer while steps remain is replaced with a safe incomplete-plan message. Each turn resets the plan before validation; a bounded previous plan may help resolve follow-ups but cannot bypass fresh planning or permission checks.

Planning adds one model call per accepted request, on top of topic reviews. It has a 20-second deadline, 1,536-token output limit, and shares the existing coordinator call budget. Question text and bounded prior-plan context are sent to the configured provider. Offline tests verify ordering and failure behavior with scripted plans; live plan quality and latency remain unverified. Restart ADK and open a fresh session after this change.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest --cov=company_assistant --cov-branch --cov-report=xml
.\.venv\Scripts\python.exe -m pip_audit --progress-spinner off
```

Offline tests block external network access; integration tests use temporary SQLite files and scripted ADK model responses. Unit tests mock IO where applicable. Coverage must be at least 80%; CI also checks source-file and changed-line coverage. The workflow tests Python 3.12 on Windows and Linux and runs a separate dependency audit. It does not deploy anything.

Paid smoke test, only after configuring a key:

```powershell
$env:RUN_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m live tests/test_agents.py
Remove-Item Env:RUN_LIVE_TESTS
```

The automated live smoke verifies a headcount round trip and an audit record. Full model-quality acceptance also requires the role-specific and combined-question checklist; offline scripted tests do not establish Cohere's routing reliability.

## Known Acceptance Blockers

- No live Cohere verification was performed during initial implementation because no API key was configured.
- The installed dependency audit reports vulnerabilities in `filelock` 3.32.7. Reported fixes require 4.2.1+, but LiteLLM 1.106.2 requires `<4`. Do not suppress the audit or force an incompatible upgrade. A compatible upstream dependency change and a fresh audit are required before release; the security CI job intentionally remains enforcing.
- Explicit `AgentTool` is supported by the pinned ADK but discouraged in newer upstream guidance. Its nested runner emits a deprecation warning about plugin arguments; the application does not suppress it.

## Troubleshooting

- **Missing/invalid identity or database:** seed first and configure a current employee ID. Process environment overrides `.env`.
- **Missing LiteLLM dependency:** this pinned LiteLLM release imports the MCP SDK on its completion path. Runtime requirements include `litellm[mcp]` to prevent `No module named 'mcp'` being wrapped as `APIConnectionError`. Reinstall the requirements in the active virtual environment and restart the server after dependency changes. Installing the SDK does not expose MCP tools or start an MCP server.
- **Local configuration changes ignored:** stop the server, remove stale terminal overrides with `Remove-Item Env:COHERE_API_KEY, Env:DEMO_USER_ID -ErrorAction SilentlyContinue`, and restart. This does not change the credentials file.
- **Model unavailable:** the terminal records `Model request failed: error_type=... status_code=...` without logging raw exception text, request content, or credentials. The UI gives safe guidance for authentication (401), access (403), model/endpoint (404), request parameters (400/422), rate/quota limits (429), and timeouts (408/504). HTTP 200 on `/run_sse` only means the event-stream request succeeded, not that the provider call succeeded. Restart after code/configuration changes, retry once, and share only this sanitized diagnostic line when troubleshooting; do not enable verbose provider logging or share your key.
- **Data/audit unavailable:** verify both configured paths and writable access to the audit directory. An audit failure blocks tool execution.
- **Permission denied:** this is expected only when the current tool returns `permission_denied`. Use the role-switching procedure rather than prompts to change identity. Agents receive the verified current employee ID and role from the identity callback, and must use successful authorized results rather than invent a restriction. If an HR query is audited as allowed but the answer still claims restricted access, restart after instruction changes and use a fresh session; do not weaken the permission matrix to work around a model-generated refusal.
- **"One part of your request was blocked by a scope check":** a specialist's scope check rejected one step of a multi-part question. Ask that part on its own. The terminal running `adk web` logs `Topic guard blocked: agent=<name> reason=out_of_scope_input` (the delegated task was rejected) or `out_of_scope_output` (the specialist's answer was rejected); send that line when reporting it.
- **Topic refused for a question that looks valid:** the scope reviewer is a model and can misjudge. It is given the company's client names so they are never treated as outside companies; if a valid question is still refused, rephrase it with the employee, client, or project name and report the wording, because the classifier needs a worked example added.
- **Topic refused:** ask only for supported company data. Separate unrelated requests, avoid asking for unavailable fields/actions, and provide an explicit entity or employee ID when a follow-up is ambiguous. A topic refusal is distinct from a role-based permission denial.
- **Monthly limit reached:** the message "The model provider's monthly usage limit for this API key has been reached" means Cohere refused the call because a Trial key is capped at 1,000 API calls a month. Waiting a minute does not help and the app does not retry. Use a Cohere Production key or wait for the monthly reset. Heavy testing (a single multi-part question can use 30 or more calls) uses the allowance quickly.
- **Rate limited:** the message "The model service is rate limited right now" means Cohere returned HTTP 429. A Cohere Trial key allows only 20 API calls per minute and one question needs roughly 8 to 12 calls. Every model call is already retried with backoff, but for a Trial key also set `MODEL_CALLS_PER_MINUTE=18` in the agent's `.env` so the app paces itself and never hits the limit. A production key needs no setting.
- **"I could not create a valid company-data plan":** the planning model call failed or its reply was not a valid plan (it is retried once with the reason). The terminal running `adk web` logs a line starting `Request planning failed` with the error type and a value-free reason such as `invalid plan (steps.0.agent: literal_error)`; send that line when reporting it.
- **Topic safety check unavailable:** the scope reviewer failed, timed out, or returned an invalid decision. No unchecked answer is released; retry later and check the sanitized terminal diagnostic. Do not disable authorization to work around it.
- **Plan unavailable or incomplete:** clarify the employee/client/project reference or narrow the request. Invalid plans and premature final answers are blocked; inspect `request_plan` and `plan_status` in session state. Planning does not grant data access and does not justify bypassing permissions.
- **OneDrive or SQLite locks:** databases now live in the project's `db/` directory and may be synced by OneDrive. Pause syncing if it causes locking conflicts; never move, overwrite, or reset databases while the server is using them. Keep this demo limited to synthetic data.

See [CHANGELOG.md](CHANGELOG.md) for implemented scope and verification limits.