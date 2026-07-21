from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus, StopReason
from app.schemas.iteration import IterationSummary, TaskResultOut


class JobExecutorConfig(BaseModel):
    """Executor-specific overrides. Unknown keys are preserved."""

    model_config = ConfigDict(extra="allow")

    agent_model: str | None = Field(default=None, description="Override model for the agent under test.")
    n_concurrent: int | None = Field(default=None, ge=1, description="Max concurrent sandbox tasks.")
    per_task_timeout: int | None = Field(
        default=None,
        ge=1,
        description="Per-task agent timeout in seconds (Harbor executor).",
    )


class JobCreate(BaseModel):
    task_ids: list[str] | None = Field(
        default=None,
        description="Benchmark task IDs to run. Defaults to the built-in subset.",
        examples=[["regex-log", "cobol-modernization"]],
    )
    max_iterations: int = Field(default=5, ge=1, le=50)
    patience: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Stop after this many consecutive iterations without score improvement.",
    )
    executor: Literal["simulated", "harbor"] = Field(
        default="simulated",
        description=(
            "simulated = deterministic fake benchmark (M1/M2/M4 dev); "
            "harbor = real Terminal-Bench via Harbor+Daytona (M3)."
        ),
    )
    config: JobExecutorConfig = Field(
        default_factory=JobExecutorConfig,
        description="Executor-specific overrides (agent_model, n_concurrent, etc.).",
    )


class JobSummary(BaseModel):
    id: str = Field(description="Job ID (32-character lowercase hex).")
    status: JobStatus
    stop_reason: StopReason | None
    task_ids: list[str]
    max_iterations: int
    patience: int
    executor: str
    best_val_score: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobResponse(JobSummary):
    """Job status plus structured benchmark results when available.

    When `status` is `completed`, `latest_task_results` is the M1 deliverable:
    which tasks passed/failed and a summary of observed failures.
    """

    learnings: str
    error: str | None
    best_agent_version_no: int | None = None
    iterations: list[IterationSummary] = Field(default_factory=list)
    latest_task_results: list[TaskResultOut] = Field(
        default_factory=list,
        description=(
            "Structured M1 result from the most recent completed iteration: "
            "per-task pass/fail status and failure summaries. Empty while the job "
            "is still queued/running with no finished iteration."
        ),
    )

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "examples": [
                {
                    "id": "a1b2c3d4e5f6789012345678abcdef01",
                    "status": "completed",
                    "stop_reason": "max_iterations",
                    "task_ids": ["regex-log", "cobol-modernization"],
                    "max_iterations": 2,
                    "patience": 2,
                    "executor": "simulated",
                    "best_val_score": 0.5,
                    "created_at": "2026-07-21T12:00:00Z",
                    "started_at": "2026-07-21T12:00:01Z",
                    "finished_at": "2026-07-21T12:01:00Z",
                    "learnings": "",
                    "error": None,
                    "best_agent_version_no": 1,
                    "iterations": [],
                    "latest_task_results": [
                        {
                            "task_id": "regex-log",
                            "reward": 1.0,
                            "status": "passed",
                            "failure_summary": None,
                        },
                        {
                            "task_id": "cobol-modernization",
                            "reward": 0.0,
                            "status": "failed",
                            "failure_summary": (
                                "Simulated failure on cobol-modernization: agent did not "
                                "explore environment or verify solution."
                            ),
                        },
                    ],
                }
            ]
        },
    }
