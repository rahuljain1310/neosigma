# Agent Optimization Service

Backend service that runs an agent against a TerminalBench subset in isolated sandboxes, mines failures from execution traces, and iteratively improves `agent/agent.py` via an LLM optimization loop.

## Architecture

```
POST /jobs → worker claims job → for each iteration:
  load best agent_version → harbor run (sandbox per task) → persist traces/results
  → LLM proposes new agent.py → save agent_version → accept if score improved
```

## Asynchronous processing (M2)

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

Postgres is the communication boundary between the API and worker. The API
writes submissions and reads status; the worker reads pending work and persists
progress and results. This keeps benchmark work out of the request path and
gives both layers one durable source of truth.

## Sandbox execution

Real benchmark jobs use the Harbor executor with Daytona as the environment
provider. The service orchestrates the run, but untrusted agent commands never
execute in the API or worker process:

```
JobProcessor
  → HarborExecutor
    → TerminalBenchRunner / Harbor
      → one isolated Daytona sandbox per task
```

### Environment lifecycle

Each job receives its own auto-harness workspace. Before an iteration, the
executor writes the selected agent version and benchmark configuration into that
workspace, clears artifacts from the previous run, and starts Harbor. Harbor
creates a separate Daytona sandbox for each task, runs the agent and verifier,
then tears the environment down.

The agent's LLM loop is controlled by Harbor; only the agent's terminal commands
cross into the sandbox. The API and worker retain no mechanism for directly
executing the generated agent code.

### Results and failure handling

Harbor writes a trace and verifier result for each task. The executor converts
those artifacts into structured task results and persists the combined process
log for diagnosis. Missing verifier output, sandbox provisioning failures, and
timeouts are recorded as `infra_error`, keeping infrastructure failures distinct
from genuine agent failures.

The executor checks prerequisites before starting, enforces run timeouts, and
terminates the full Harbor process group when a job is cancelled or exceeds its
deadline. This prevents abandoned benchmark processes while preserving whatever
diagnostic output was available.

The `simulated` executor follows the same interface without creating sandboxes,
allowing the API, worker, and optimization lifecycle to be tested without
external infrastructure or sandbox cost.

## Iterative optimizer loop (M4)

The processor treats every benchmark run as an immutable iteration and every
agent proposal as a versioned snapshot:

```
baseline agent
  → benchmark
  → analyze results and traces
  → propose a complete agent.py
  → benchmark the candidate
  → accept if its score improves
  → repeat from the best version
```

### Baseline and proposal

Iteration 0 benchmarks the original agent template and establishes the initial
best score. For each subsequent proposal, the optimizer receives the current
best agent, task outcomes and traces, accumulated learnings, and the effect of
the previous attempt. It returns a complete replacement for `agent.py` together
with its rationale and new learnings.

The proposal is stored as a new `AgentVersion` linked to its parent, including
its content hash and diff. The next iteration benchmarks that exact snapshot.
This means rejected proposals remain inspectable and runs can be reconstructed
without relying on mutable files.

### Evaluation and stopping

A candidate becomes the new best version only when its validation score strictly
improves. Otherwise it is rejected, the previous best remains active, and the
failed attempt is fed back into the next proposal. The loop stops when all tasks
pass, the iteration limit is reached, repeated attempts do not improve the
score, the optimizer produces no change, or the job is cancelled.

Each phase is committed as it completes. Iteration records retain benchmark
scores, acceptance decisions, task results, traces, optimizer inputs and
outputs, rationale, learnings, and timing information. Clients can inspect the
entire history through the iteration and agent-version APIs while the job is
running or after it finishes.

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
| `GET /jobs/{id}/agent-versions` | Full agent-version history |
| `GET /jobs/{id}/agent-versions/{n}` | Agent snapshot and diff |
| `POST /jobs/{id}/cancel` | Cancel a queued/running job |

Auth: log in with `POST /auth/login`, then send `Authorization: Bearer <access_token>`. Members see only their own jobs; admins see all org jobs.

## Executors

| Executor | Use case |
|----------|----------|
| `simulated` | Deterministic fake benchmark for M1/M2/M4 dev |
| `harbor` | Real Terminal-Bench via auto-harness + Harbor sandboxes (M3) |

Harbor mode clones [neosigmaai/auto-harness](https://github.com/neosigmaai/auto-harness) and runs `harbor run --env daytona` ([TB 2.0 docs](https://www.tbench.ai/docs/run-terminal-bench-2-0)). Requires `DAYTONA_API_KEY` and `OPENAI_API_KEY` (for the agent under test).

## TerminalBench task subset

Testing used sets of 3–12 tasks drawn from:

`regex-log`, `extract-elf`, `log-summary-date-ranges`, `openssl-selfsigned-cert`,
`sqlite-db-truncate`, `fix-code-vulnerability`, `password-recovery`,
`cancel-async-tasks`, `query-optimize`, `gcode-to-text`, `filter-js-from-html`,
`break-filter-js-from-html`, `fix-git`, `git-leak-recovery`, `git-multibranch`,
`sqlite-with-gcov`, `cobol-modernization`, `path-tracing`, `qemu-alpine`,
`configure-git-webserver`, `extract-moves-from-video`, `hf-model-inference`

The default is a smaller set (`regex-log`, `extract-elf`,
`log-summary-date-ranges`). Selection favored shorter execution times and enough
variance in success rates to exercise the optimization loop. Override via
`POST /jobs` `task_ids`.


### Sample Run 

```
uv run python test_client.py --executor harbor --max-iterations 15 --patience 5
Health: {'status': 'ok', 'version': '0.1.0'}
Logged in as admin@example.com @ default
Submitted job bdfb8b9e52fe4e9ea040ed147d0ad0a9 (status=queued)
  job status=queued best_val_score=None
  job status=running best_val_score=None
  [iter 0] running benchmark with agent_v=0 | tasks: 3 pending, 0 running, 0 completed (0/3)
  [iter 0] benchmark done: agent_v=0 val_score=0.667 | 2/3 passed, 1 failed, 0 infra_error (log-summary-date-ranges)
  [iter 0] proposed agent_v=1 for next run: The failed run involved a date-range task where the agent appeared to infer a reference date from the data instead of reliably using the container's current ...
  [iter 1] running benchmark with agent_v=1 | tasks: 3 pending, 0 running, 0 completed (0/3)
  [iter 1] benchmark done: agent_v=1 val_score=0.667 | 2/3 passed, 1 failed, 0 infra_error (regex-log)
  [iter 1] rejected — val_score=0.667 (best remains 0.667)
  [iter 1] proposed agent_v=2 for next run: The failed date-range task used a guessed reference date instead of the container's actual current date. The prior broad attempt fixed that task but added in...
  [iter 2] running benchmark with agent_v=2 | tasks: 3 pending, 0 running, 0 completed (0/3)
  job status=completed best_val_score=1.000
  [iter 2] benchmark done: agent_v=2 val_score=1.000 | 3/3 passed, 0 failed, 0 infra_error
  [iter 2] accepted — new best_val_score=1.000 (agent_v=2)

=== Job summary ===
{
  "id": "bdfb8b9e52fe4e9ea040ed147d0ad0a9",
  "status": "completed",
  "stop_reason": "all_tasks_passed",
  "best_val_score": 1.0,
  "best_agent_version_no": 2,
  "task_ids": [
    "regex-log",
    "extract-elf",
    "log-summary-date-ranges"
  ]
}

=== Latest task results ===
  regex-log: passed reward=1.0
  extract-elf: passed reward=1.0
  log-summary-date-ranges: passed reward=1.0

=== Iteration history (3 iterations) ===
  [✓] iter=0 phase=done val_score=0.6666666666666666 agent_v=0 | 2/3 passed, 1 failed, 0 infra_error
       failed: log-summary-date-ranges
       changes: The failed run involved a date-range task where the agent appeared to infer a reference date from the data instead of reliably using the container's current dat
  [✗] iter=1 phase=done val_score=0.6666666666666666 agent_v=1 | 2/3 passed, 1 failed, 0 infra_error
       failed: regex-log
       changes: The failed date-range task used a guessed reference date instead of the container's actual current date. The prior broad attempt fixed that task but added initi
  [✓] iter=2 phase=done val_score=1.0 agent_v=2 | 3/3 passed, 0 failed, 0 infra_error
```