from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.job import JobStatus, StopReason
from app.schemas.iteration import IterationSummary, TaskResultOut


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
        description="simulated = deterministic fake benchmark (M1/M2/M4 dev); harbor = real Terminal-Bench via Harbor+Daytona (M3).",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Executor-specific overrides (agent_model, n_concurrent, etc.).",
    )


class JobSummary(BaseModel):
    id: str
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
    learnings: str
    error: str | None
    best_agent_version_no: int | None = None
    iterations: list[IterationSummary] = Field(default_factory=list)
    latest_task_results: list[TaskResultOut] = Field(
        default_factory=list,
        description="Per-task results from the most recent completed iteration.",
    )
