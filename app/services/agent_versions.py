from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.harness.workspace import sha256_text, unified_diff
from app.models.agent_version import AgentVersion
from app.models.iteration import Iteration, IterationPhase
from app.models.job import Job
from app.models.task_result import TaskResult, TaskStatus


async def create_agent_version(
    session: AsyncSession,
    *,
    job_id: str,
    version_no: int,
    content: str,
    parent_version_no: int | None = None,
    parent_content: str | None = None,
    created_by_iteration: int | None = None,
) -> AgentVersion:
    diff = unified_diff(parent_content, content) if parent_content is not None else None
    version = AgentVersion(
        job_id=job_id,
        version_no=version_no,
        parent_version_no=parent_version_no,
        content=content,
        sha256=sha256_text(content),
        diff=diff,
        created_by_iteration=created_by_iteration,
    )
    session.add(version)
    await session.flush()
    return version


async def get_agent_version(session: AsyncSession, job_id: str, version_no: int) -> AgentVersion | None:
    result = await session.execute(
        select(AgentVersion).where(
            AgentVersion.job_id == job_id,
            AgentVersion.version_no == version_no,
        )
    )
    return result.scalar_one_or_none()


async def list_agent_versions(session: AsyncSession, job_id: str) -> list[AgentVersion]:
    result = await session.execute(
        select(AgentVersion)
        .where(AgentVersion.job_id == job_id)
        .order_by(AgentVersion.version_no)
    )
    return list(result.scalars().all())


async def save_iteration_results(
    session: AsyncSession,
    *,
    iteration: Iteration,
    job: Job,
    benchmark_result,
) -> None:
    for tr in benchmark_result.task_results:
        session.add(
            TaskResult(
                iteration_id=iteration.id,
                job_id=job.id,
                task_id=tr.task_id,
                reward=tr.reward,
                status=TaskStatus(tr.status),
                failure_summary=tr.failure_summary,
                trace=tr.trace,
                verifier_result=tr.verifier_result,
            )
        )
    iteration.val_score = benchmark_result.val_score
    iteration.executor_log = benchmark_result.executor_log
    iteration.bench_finished_at = datetime.now(timezone.utc)
    iteration.phase = IterationPhase.ANALYZING

