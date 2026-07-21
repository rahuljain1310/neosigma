from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.iteration import IterationPhase
from app.models.task_result import TaskStatus


class TaskResultOut(BaseModel):
    task_id: str
    reward: float | None
    status: TaskStatus
    failure_summary: str | None = None
    trace: dict[str, Any] | list[Any] | None = None
    verifier_result: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class IterationSummary(BaseModel):
    id: str
    iteration_no: int
    agent_version_no: int
    phase: IterationPhase
    val_score: float | None
    accepted: bool | None
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
    task_results: list[TaskResultOut] = Field(default_factory=list)
