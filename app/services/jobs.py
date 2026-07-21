from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_version import AgentVersion
from app.models.iteration import Iteration
from app.models.task_result import TaskResult
from app.schemas.iteration import IterationDetail, IterationSummary, TaskResultOut
from app.schemas.job import JobResponse, JobSummary


async def get_iterations(session: AsyncSession, job_id: str) -> list[Iteration]:
    result = await session.execute(
        select(Iteration)
        .where(Iteration.job_id == job_id)
        .order_by(Iteration.iteration_no)
    )
    return list(result.scalars().all())


async def get_iteration(
    session: AsyncSession, job_id: str, iteration_no: int
) -> Iteration | None:
    result = await session.execute(
        select(Iteration).where(
            Iteration.job_id == job_id,
            Iteration.iteration_no == iteration_no,
        )
    )
    return result.scalar_one_or_none()


async def get_task_results_for_iteration(
    session: AsyncSession, iteration_id: str
) -> list[TaskResult]:
    result = await session.execute(
        select(TaskResult).where(TaskResult.iteration_id == iteration_id)
    )
    return list(result.scalars().all())


async def get_latest_task_results(session: AsyncSession, job_id: str) -> list[TaskResult]:
    result = await session.execute(
        select(Iteration)
        .where(Iteration.job_id == job_id)
        .order_by(Iteration.iteration_no.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest is None:
        return []
    return await get_task_results_for_iteration(session, latest.id)


async def best_agent_version_no(session: AsyncSession, job) -> int | None:
    if not job.best_agent_version_id:
        return None
    result = await session.execute(
        select(AgentVersion).where(AgentVersion.id == job.best_agent_version_id)
    )
    av = result.scalar_one_or_none()
    return av.version_no if av else None


def job_to_response(
    job,
    *,
    iterations: list[Iteration] | None = None,
    latest_task_results: list[TaskResult] | None = None,
    best_version_no: int | None = None,
) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        stop_reason=job.stop_reason,
        task_ids=job.task_ids,
        max_iterations=job.max_iterations,
        patience=job.patience,
        executor=job.executor,
        best_val_score=job.best_val_score,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        learnings=job.learnings,
        error=job.error,
        best_agent_version_no=best_version_no,
        iterations=[IterationSummary.model_validate(i) for i in (iterations or [])],
        latest_task_results=[
            TaskResultOut.model_validate(t) for t in (latest_task_results or [])
        ],
    )


def iteration_to_detail(iteration: Iteration, task_results: list[TaskResult]) -> IterationDetail:
    return IterationDetail(
        **IterationSummary.model_validate(iteration).model_dump(),
        llm_prompt=iteration.llm_prompt,
        llm_response=iteration.llm_response,
        executor_log=iteration.executor_log,
        task_results=[TaskResultOut.model_validate(t) for t in task_results],
    )
