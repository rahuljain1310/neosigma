from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.harness.template import DEFAULT_TASK_IDS
from app.models.job import Job, JobStatus, StopReason
from app.models.user import Role, User
from app.schemas.agent_version import AgentVersionDetail
from app.schemas.iteration import IterationDetail, IterationSummary
from app.schemas.job import JobCreate, JobResponse, JobSummary
from app.services.agent_versions import get_agent_version, list_agent_versions
from app.services.jobs import (
    best_agent_version_no,
    build_iteration_summaries,
    get_iteration,
    get_iterations,
    get_latest_task_results,
    get_task_results_for_iteration,
    iteration_to_detail,
    job_to_response,
    proposed_version_no,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a benchmark optimization job",
)
async def create_job(
    body: JobCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobResponse:
    task_ids = body.task_ids or DEFAULT_TASK_IDS
    job = Job(
        org_id=user.org_id,
        created_by=user.id,
        status=JobStatus.QUEUED,
        task_ids=task_ids,
        max_iterations=body.max_iterations,
        patience=body.patience,
        executor=body.executor,
        config=body.config.model_dump(mode="python", exclude_none=True),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job_to_response(job)


@router.get("", response_model=list[JobSummary], summary="List jobs visible to the current user")
async def list_jobs(
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[JobSummary]:
    stmt = select(Job).where(Job.org_id == user.org_id)
    if user.role != Role.ADMIN:
        stmt = stmt.where(Job.created_by == user.id)
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    stmt = stmt.order_by(Job.created_at.desc())
    result = await session.execute(stmt)
    return [JobSummary.model_validate(job) for job in result.scalars().all()]


@router.get("/{job_id}", response_model=JobResponse, summary="Get job status and latest results")
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobResponse:
    job = await _get_visible_job(session, job_id, user)
    iterations = await get_iterations(session, job_id)
    iteration_summaries = await build_iteration_summaries(session, job_id, iterations)
    latest_results = await get_latest_task_results(session, job_id)
    version_no = await best_agent_version_no(session, job)
    return job_to_response(
        job,
        iteration_summaries=iteration_summaries,
        latest_task_results=latest_results,
        best_version_no=version_no,
    )


@router.get(
    "/{job_id}/iterations",
    response_model=list[IterationSummary],
    summary="List all iterations for a job",
)
async def list_iterations(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[IterationSummary]:
    await _get_visible_job(session, job_id, user)
    iterations = await get_iterations(session, job_id)
    return await build_iteration_summaries(session, job_id, iterations)


@router.get(
    "/{job_id}/iterations/{iteration_no}",
    response_model=IterationDetail,
    summary="Get full detail for one iteration (traces, LLM artifacts)",
)
async def get_iteration_detail(
    job_id: str,
    iteration_no: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> IterationDetail:
    await _get_visible_job(session, job_id, user)
    iteration = await get_iteration(session, job_id, iteration_no)
    if iteration is None:
        raise HTTPException(status_code=404, detail="Iteration not found")
    job = await session.get(Job, job_id)
    task_results = await get_task_results_for_iteration(session, iteration.id)
    proposed = await proposed_version_no(session, job_id, iteration.iteration_no)
    return iteration_to_detail(
        iteration,
        task_results,
        all_task_ids=list(job.task_ids) if job is not None else None,
        proposed_agent_version_no=proposed,
    )


@router.get(
    "/{job_id}/agent-versions",
    response_model=list[AgentVersionDetail],
    summary="List all agent versions for a job",
)
async def list_job_agent_versions(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[AgentVersionDetail]:
    await _get_visible_job(session, job_id, user)
    versions = await list_agent_versions(session, job_id)
    return [AgentVersionDetail.model_validate(v) for v in versions]


@router.get(
    "/{job_id}/agent-versions/{version_no}",
    response_model=AgentVersionDetail,
    summary="Get one agent version (full agent.py + diff)",
)
async def get_job_agent_version(
    job_id: str,
    version_no: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> AgentVersionDetail:
    await _get_visible_job(session, job_id, user)
    version = await get_agent_version(session, job_id, version_no)
    if version is None:
        raise HTTPException(status_code=404, detail="Agent version not found")
    return AgentVersionDetail.model_validate(version)


@router.post("/{job_id}/cancel", response_model=JobResponse, summary="Cancel a queued or running job")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobResponse:
    job = await _get_visible_job(session, job_id, user)
    if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail=f"Job already {job.status.value}")
    job.status = JobStatus.CANCELLED
    job.stop_reason = StopReason.CANCELLED
    job.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)
    return job_to_response(job)


async def _get_visible_job(session: AsyncSession, job_id: str, user: User) -> Job:
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != Role.ADMIN and job.created_by != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this job")
    return job
