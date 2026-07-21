# Agent Optimization Service

Backend service that runs an agent against a TerminalBench subset in isolated sandboxes, mines failures from execution traces, and iteratively improves `agent/agent.py` via an LLM optimization loop.

## Architecture

```
POST /jobs → worker claims job → for each iteration:
  load best agent_version → harbor run (sandbox per task) → persist traces/results
  → LLM proposes new agent.py → save agent_version → accept if score improved
```

**Key design decision:** auto-harness has no structured improvement API. Improvements are direct edits to the full `agent/agent.py` file. Our service owns a working copy of the harness and plays the "coding agent" role programmatically. Postgres stores every `agent_version`, iteration, trace, and LLM artifact for full observability.

**Sandbox boundary:** The API/worker never executes agent code. `TerminalBenchRunner` shells out to `harbor run`, which provisions one Daytona sandbox per task. The agent LLM loop runs in the Harbor process; only bash commands enter the sandbox.

## Asynchronous processing

Submitting a run does not block on benchmark execution. `POST /jobs` creates a
`queued` job in Postgres and immediately returns `202 Accepted` with the job ID.
The client uses that ID to poll `GET /jobs/{id}` for status and results.

```
Client ── submit ──> API ── queued job ──> Postgres
Client <── status/results ── API <──────────┘
                                         │
                           Worker ── claim/process/update
```

### Persistence model

Postgres stores the queue, lifecycle, progress, and results. The main
relationships are:

```
Organization ──< User ──< Job
                         ├──< Iteration ──< TaskResult
                         └──< AgentVersion
```

- **Job** is the durable unit of work. It records its owner, lifecycle status,
  timestamps, stop reason or error, requested tasks, and best score/version.
- **Iteration** records each benchmark/optimization cycle, its current phase,
  live task progress, score, and phase timestamps.
- **TaskResult** stores the final outcome and trace for each task in an
  iteration.
- **AgentVersion** stores every complete agent revision so accepted and rejected
  attempts remain reproducible.

The job moves through the following lifecycle:

```
queued → running → completed
                 → failed
queued/running   → cancelled
```

### How jobs are claimed and processed

The FastAPI lifespan starts one asynchronous worker alongside the API. Its loop
selects the oldest queued job and conditionally changes it from `queued` to
`running` before handing its ID to the job processor. If no work is available,
the worker waits briefly and checks again.

The processor reloads the job, runs its benchmark and optimization iterations,
and commits each meaningful state transition. On success it marks the job
`completed`; unexpected errors are persisted and mark it `failed`. Cancellation
also travels through Postgres, allowing the processor and benchmark executor to
observe it without a direct call from the API process.

### How progress stays current

Before a benchmark starts, the processor creates an iteration with its phase and
the task IDs waiting to run. While Harbor is running, it observes which tasks
are pending or active. Whenever that set changes, a progress callback updates
the iteration through a fresh database session, so polling requests can read
progress while the main processor is still busy.

The API derives completed tasks from those live sets. Once a benchmark finishes,
the processor persists the score and per-task passed, failed, or infrastructure
error results, then advances the iteration phase. Job-level fields such as the
best score, best agent version, stop reason, and final status are updated as the
optimization loop progresses.

### Status APIs

- `POST /jobs` — submit a job and receive `202 Accepted` with its ID.
- `GET /jobs/{id}` — primary polling endpoint; returns lifecycle status, current
  iteration summaries, live task progress, latest task results, and best score.
- `GET /jobs?status=...` — list visible jobs, optionally filtered by lifecycle
  status.
- `GET /jobs/{id}/iterations` — read the complete iteration history.
- `GET /jobs/{id}/iterations/{n}` — inspect one iteration and its task results,
  traces, and optimizer artifacts.
- `GET /jobs/{id}/agent-versions` — inspect the agent revisions produced by the
  run.
- `POST /jobs/{id}/cancel` — request cancellation of queued or running work.

All reads use the same persisted state written by the worker and enforce the
caller's organization and job visibility rules.

Postgres is therefore the communication boundary between the API and worker.
The API writes submissions and reads status; the worker reads pending work and
persists progress and results. This keeps benchmark work out of the request path
and gives both layers one durable source of truth.

For this project, the worker runs alongside the API and the database acts as the
queue. A production deployment requiring multiple workers or horizontal scaling
would move job delivery to a dedicated queue such as Redis/Celery while keeping
the same submit-and-poll API.

## Quick start

### 1. Start Postgres

```bash
docker compose up -d db
```

If the schema changed (e.g. iteration columns), wipe the volume so Postgres is recreated cleanly:

```bash
docker compose down -v
docker compose up -d db
```

### 2. Install dependencies

```bash
uv sync
cp .env.example .env
# edit .env if needed
```

### 3. Run the API

```bash
uv run uvicorn app.main:app --reload --port 8000
```

On first startup the service seeds a default org and admin user:

| Field | Value |
|-------|-------|
| Organization | `default` |
| Email | `admin@example.com` |
| Password | `assignment-password` |

Log in via `POST /auth/login`, then use the returned `access_token` as `Authorization: Bearer <token>`.

### 4. Run the test client (simulated executor — no sandbox cost)

```bash
make client
# or: uv run python test_client.py
```

The test client logs in with the default credentials above — no env vars required.

### 5. OpenAPI spec

- Swagger UI: http://localhost:8000/docs
- Raw spec: http://localhost:8000/openapi.json
- Export to file: `uv run python scripts/export_openapi.py`

## API overview

| Endpoint | Description |
|----------|-------------|
| `POST /auth/login` | Log in to an organization (returns access + refresh tokens) |
| `POST /auth/refresh` | Refresh access token |
| `POST /auth/logout` | Revoke a refresh token |
| `GET /auth/me` | Current user profile (includes `org_name`) |
| `POST /orgs` | Create an organization (public bootstrap) |
| `POST /orgs/{id}/users` | Create a user (first user open; then org admin only) |
| `POST /jobs` | Submit optimization job (returns 202) |
| `GET /jobs` | List jobs (optional `?status=` filter) |
| `GET /jobs/{id}` | Poll job status, latest results, iteration summaries |
| `GET /jobs/{id}/iterations` | Full iteration history |
| `GET /jobs/{id}/iterations/{n}` | Iteration detail with traces + LLM artifacts |
| `POST /jobs/{id}/cancel` | Cancel a queued/running job |

Auth: log in with `POST /auth/login`, then send `Authorization: Bearer <access_token>`. Members see only their own jobs; admins see all org jobs.

## Executors

| Executor | Use case |
|----------|----------|
| `simulated` | Deterministic fake benchmark for M1/M2/M4 dev |
| `harbor` | Real Terminal-Bench via auto-harness + Harbor sandboxes (M3) |

Harbor mode clones [neosigmaai/auto-harness](https://github.com/neosigmaai/auto-harness) and runs `harbor run --env daytona` ([TB 2.0 docs](https://www.tbench.ai/docs/run-terminal-bench-2-0)). Requires `DAYTONA_API_KEY` and `OPENAI_API_KEY` (for the agent under test).

## TerminalBench task subset

Default 10-task subset (fast, representative mix):

- `regex-log`, `cobol-modernization`, `git-multibranch`, `sqlite-with-gcov`, `path-tracing`
- `qemu-alpine`, `configure-git-webserver`, `extract-moves-from-video`, `fix-git`, `hf-model-inference`

Override via `POST /jobs` `task_ids` field.

## Optimization loop

1. **Baseline (iteration 0):** run template `agent.py`, record score + traces.
2. **Iterations 1..N:** optimizer reads failing traces + accumulated learnings → proposes full new `agent.py` → run benchmark → accept if `val_score` improved, else reject.
3. **Stop when:** patience exhausted (N rounds without improvement), `max_iterations` reached, all tasks pass, or cancelled.

All phases persist to Postgres as they complete (`bench_started_at`, `optimizer_finished_at`, etc.) for live observability of async runs.

## Differences from auto-harness

| auto-harness | This service |
|--------------|--------------|
| Human drives Claude Code via PROGRAM.md | Fully autonomous HTTP service |
| git commits as version store | `agent_versions` table in Postgres |
| `results.tsv` one line per success | Full history including rejections + traces |
| Held-out test split gating | Same-subset improvement (noted limitation) |
| `learnings.md` on disk | `jobs.learnings` in DB, fed to every optimizer prompt |

## Project structure

```
app/
  api/           FastAPI routers
  models/        SQLAlchemy models (one file per model)
  schemas/       Pydantic request/response models
  executor/      SimulatedExecutor + HarborExecutor
  harness/       Agent template + workspace adapter
  worker/        Background job processor
  optimizer.py   LLM/heuristic improvement proposer
test_client.py   End-to-end client
scripts/         OpenAPI export
```

## Future work

- Held-out test split gating to prevent overfitting on the API-provided subset
- Regression suite promotion (auto-harness's `gating.py` Step 3)
- Alembic migrations for production schema evolution
- Job queue via Redis/Celery for horizontal worker scaling

## Not implemented / trade-offs

- **Alembic:** using `create_all` for scope; fine for take-home, not production-grade migrations
- **Test split gating:** assignment provides one subset via API; we gate on same-subset improvement
- **Harbor in CI:** requires Docker + harbor CLI; simulated executor covers automated testing
