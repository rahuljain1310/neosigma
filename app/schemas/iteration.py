from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.iteration import IterationPhase
from app.models.task_result import TaskStatus


class TraceMessage(BaseModel):
    """One turn in an agent conversation trace."""

    model_config = ConfigDict(extra="allow")

    role: str | None = Field(default=None, description="Message role (system, user, assistant, tool, …).")
    content: Any | None = Field(default=None, description="Message content (string or structured parts).")


class TraceEnvelope(BaseModel):
    """Harbor-style wrapper when the trace is not a bare message list."""

    model_config = ConfigDict(extra="allow")

    messages: list[TraceMessage] | None = None
    trace: list[TraceMessage] | dict[str, Any] | None = None


class VerifierRewards(BaseModel):
    model_config = ConfigDict(extra="allow")

    reward: float | None = None


class VerifierInner(BaseModel):
    model_config = ConfigDict(extra="allow")

    rewards: VerifierRewards | None = None


class VerifierResult(BaseModel):
    """Sandbox verifier output for a single task."""

    model_config = ConfigDict(extra="allow")

    task_name: str | None = None
    verifier_result: VerifierInner | dict[str, Any] | None = None


class OptimizerTaskSnippet(BaseModel):
    """Compact task context fed into the optimizer prompt."""

    model_config = ConfigDict(extra="allow")

    task_id: str | None = None
    status: str | None = None
    failure_summary: str | None = None
    reward: float | None = None
    trace: Any | None = None
    verifier: Any | None = None


class OptimizerContext(BaseModel):
    """Snapshot of inputs the optimizer used for one proposal."""

    model_config = ConfigDict(extra="allow")

    source_agent_version_no: int | None = None
    failing_task_count: int | None = None
    passed_task_count: int | None = None
    infra_error_count: int | None = None
    failure_context: list[OptimizerTaskSnippet] | list[dict[str, Any]] | None = None
    success_context: list[OptimizerTaskSnippet] | list[dict[str, Any]] | None = None
    infra_context: list[OptimizerTaskSnippet] | list[dict[str, Any]] | None = None
    recent_attempt: Any | None = None
    optimizer_mode: str | None = None
    configured_optimizer_mode: str | None = None
    optimizer_model: str | None = None
    agent_model: str | None = None
    prompt_chars: int | None = None
    fallback_reason: str | None = None


class TaskResultOut(BaseModel):
    """Structured per-task benchmark result (M1 output contract)."""

    task_id: str = Field(description="TerminalBench task identifier.")
    reward: float | None = Field(description="Task reward in [0, 1], or null if unavailable.")
    status: TaskStatus = Field(description="passed | failed | infra_error.")
    failure_summary: str | None = Field(
        default=None,
        description="Short human-readable summary of why the task failed (null when passed).",
    )
    trace: list[TraceMessage] | TraceEnvelope | dict[str, Any] | None = Field(
        default=None,
        description="Agent conversation / tool-call trace for this task.",
    )
    verifier_result: VerifierResult | dict[str, Any] | None = Field(
        default=None,
        description="Raw verifier payload from the sandbox runner.",
    )

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "examples": [
                {
                    "task_id": "regex-log",
                    "reward": 1.0,
                    "status": "passed",
                    "failure_summary": None,
                    "trace": [
                        {"role": "user", "content": "Task: regex-log"},
                        {"role": "assistant", "content": "Explored environment and attempted solution."},
                    ],
                    "verifier_result": {
                        "task_name": "regex-log",
                        "verifier_result": {"rewards": {"reward": 1.0}},
                    },
                },
                {
                    "task_id": "cobol-modernization",
                    "reward": 0.0,
                    "status": "failed",
                    "failure_summary": (
                        "Simulated failure on cobol-modernization: agent did not explore "
                        "environment or verify solution."
                    ),
                    "trace": [
                        {"role": "user", "content": "Task: cobol-modernization"},
                        {"role": "assistant", "content": "Gave up early without verifying."},
                    ],
                    "verifier_result": {
                        "task_name": "cobol-modernization",
                        "verifier_result": {"rewards": {"reward": 0.0}},
                    },
                },
            ]
        },
    }


class IterationSummary(BaseModel):
    id: str
    iteration_no: int
    agent_version_no: int
    phase: IterationPhase
    val_score: float | None
    accepted: bool | None
    tasks_pending: int = 0
    tasks_running: int = 0
    tasks_completed: int = 0
    tasks_passed: int = 0
    tasks_failed: int = 0
    tasks_infra_error: int = 0
    failed_task_ids: list[str] = Field(default_factory=list)
    proposed_agent_version_no: int | None = Field(
        default=None,
        description="Agent version created by this iteration's optimizer proposal.",
    )
    bench_started_at: datetime | None
    bench_finished_at: datetime | None
    llm_started_at: datetime | None
    llm_finished_at: datetime | None
    improvement_rationale: str | None
    learnings: str | None
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class IterationDetail(IterationSummary):
    llm_prompt: str | None = None
    llm_response: str | None = None
    executor_log: str | None = None
    optimizer_context: OptimizerContext | dict[str, Any] | None = Field(
        default=None,
        description="Failing tasks/traces and source agent version fed to the optimizer.",
    )
    task_results: list[TaskResultOut] = Field(
        default_factory=list,
        description="Per-task structured results for this iteration (M1 contract).",
    )
