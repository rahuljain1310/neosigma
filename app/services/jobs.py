from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_version import AgentVersion
from app.models.iteration import Iteration
from app.models.task_result import TaskResult, TaskStatus
from app.schemas.iteration import IterationDetail, IterationSummary, TaskResultOut
from app.schemas.job import JobResponse, JobSummary


async def get_iterations(session: AsyncSession, job_id: str) -> list[Iteration]:
    result = await session.execute(select(Iteration).where(Iteration.job_id == job_id).order_by(Iteration.iteration_no))
    return list(result.scalars().all())


async def get_iteration(session: AsyncSession, job_id: str, iteration_no: int) -> Iteration | None:
    result = await session.execute(
        select(Iteration).where(
            Iteration.job_id == job_id,
            Iteration.iteration_no == iteration_no,
        )
    )
    return result.scalar_one_or_none()


async def get_task_results_for_iteration(session: AsyncSession, iteration_id: str) -> list[TaskResult]:
    result = await session.execute(select(TaskResult).where(TaskResult.iteration_id == iteration_id))
    return list(result.scalars().all())


async def get_latest_task_results(session: AsyncSession, job_id: str) -> list[TaskResult]:
    result = await session.execute(
        select(Iteration).where(Iteration.job_id == job_id).order_by(Iteration.iteration_no.desc()).limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest is None:
        return []
    return await get_task_results_for_iteration(session, latest.id)


async def best_agent_version_no(session: AsyncSession, job) -> int | None:
    if not job.best_agent_version_id:
        return None
    result = await session.execute(select(AgentVersion).where(AgentVersion.id == job.best_agent_version_id))
    av = result.scalar_one_or_none()
    return av.version_no if av else None


async def _task_stats_by_iteration(
    session: AsyncSession, iteration_ids: list[str]
) -> dict[str, dict[str, int | list[str]]]:
    if not iteration_ids:
        return {}

    result = await session.execute(select(TaskResult).where(TaskResult.iteration_id.in_(iteration_ids)))
    stats: dict[str, dict[str, int | list[str]]] = defaultdict(
        lambda: {
            "tasks_passed": 0,
            "tasks_failed": 0,
            "tasks_infra_error": 0,
            "failed_task_ids": [],
        }
    )
    for tr in result.scalars().all():
        bucket = stats[tr.iteration_id]
        if tr.status == TaskStatus.PASSED:
            bucket["tasks_passed"] += 1
        elif tr.status == TaskStatus.INFRA_ERROR:
            bucket["tasks_infra_error"] += 1
        else:
            bucket["tasks_failed"] += 1
            bucket["failed_task_ids"].append(tr.task_id)
    return stats


async def _proposed_versions_by_iteration(session: AsyncSession, job_id: str) -> dict[int, int]:
    result = await session.execute(
        select(AgentVersion).where(
            AgentVersion.job_id == job_id,
            AgentVersion.created_by_iteration.is_not(None),
        )
    )
    proposed: dict[int, int] = {}
    for av in result.scalars().all():
        if av.created_by_iteration is None:
            continue
        current = proposed.get(av.created_by_iteration)
        if current is None or av.version_no > current:
            proposed[av.created_by_iteration] = av.version_no
    return proposed


def _iteration_to_summary(
    iteration: Iteration,
    *,
    task_stats: dict[str, int | list[str]] | None = None,
    proposed_agent_version_no: int | None = None,
) -> IterationSummary:
    stats = task_stats or {}
    return IterationSummary(
        id=iteration.id,
        iteration_no=iteration.iteration_no,
        agent_version_no=iteration.agent_version_no,
        phase=iteration.phase,
        val_score=iteration.val_score,
        accepted=iteration.accepted,
        tasks_pending=iteration.tasks_pending,
        tasks_running=iteration.tasks_running,
        tasks_completed=iteration.tasks_completed,
        tasks_passed=int(stats.get("tasks_passed", 0)),
        tasks_failed=int(stats.get("tasks_failed", 0)),
        tasks_infra_error=int(stats.get("tasks_infra_error", 0)),
        failed_task_ids=list(stats.get("failed_task_ids", [])),
        proposed_agent_version_no=proposed_agent_version_no,
        bench_started_at=iteration.bench_started_at,
        bench_finished_at=iteration.bench_finished_at,
        llm_started_at=iteration.llm_started_at,
        llm_finished_at=iteration.llm_finished_at,
        improvement_rationale=iteration.improvement_rationale,
        learnings=iteration.learnings,
        error=iteration.error,
        created_at=iteration.created_at,
    )


async def proposed_version_no(session: AsyncSession, job_id: str, iteration_no: int) -> int | None:
    result = await session.execute(
        select(AgentVersion.version_no)
        .where(
            AgentVersion.job_id == job_id,
            AgentVersion.created_by_iteration == iteration_no,
        )
        .order_by(AgentVersion.version_no.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def build_iteration_summaries(
    session: AsyncSession, job_id: str, iterations: list[Iteration]
) -> list[IterationSummary]:
    stats_by_iteration = await _task_stats_by_iteration(session, [i.id for i in iterations])
    proposed_by_iteration = await _proposed_versions_by_iteration(session, job_id)
    return [
        _iteration_to_summary(
            iteration,
            task_stats=stats_by_iteration.get(iteration.id),
            proposed_agent_version_no=proposed_by_iteration.get(iteration.iteration_no),
        )
        for iteration in iterations
    ]


def job_to_response(
    job,
    *,
    iteration_summaries: list[IterationSummary] | None = None,
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
        iterations=iteration_summaries or [],
        latest_task_results=[TaskResultOut.model_validate(t) for t in (latest_task_results or [])],
    )


def iteration_to_detail(
    iteration: Iteration,
    task_results: list[TaskResult],
    *,
    proposed_agent_version_no: int | None = None,
) -> IterationDetail:
    stats = {
        "tasks_passed": sum(1 for t in task_results if t.status == TaskStatus.PASSED),
        "tasks_failed": sum(1 for t in task_results if t.status == TaskStatus.FAILED),
        "tasks_infra_error": sum(1 for t in task_results if t.status == TaskStatus.INFRA_ERROR),
        "failed_task_ids": [t.task_id for t in task_results if t.status in {TaskStatus.FAILED, TaskStatus.INFRA_ERROR}],
    }
    return IterationDetail(
        **_iteration_to_summary(
            iteration,
            task_stats=stats,
            proposed_agent_version_no=proposed_agent_version_no,
        ).model_dump(),
        llm_prompt=iteration.llm_prompt,
        llm_response=iteration.llm_response,
        executor_log=iteration.executor_log,
        optimizer_context=iteration.optimizer_context,
        task_results=[TaskResultOut.model_validate(t) for t in task_results],
    )
