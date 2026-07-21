from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.harness.template import DEFAULT_TASK_IDS
from app.models.job import Job, JobStatus
from app.models.user import Role, User
from app.schemas.common import ErrorResponse
from app.schemas.iteration import IterationDetail, IterationSummary
from app.schemas.job import JobCreate, JobResponse
from app.services.jobs import (
    best_agent_version_no,
    get_iteration,
    get_iterations,
    get_latest_task_results,
    get_task_results_for_iteration,
    iteration_to_detail,
    job_to_response,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: {"model": ErrorResponse}},
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
        config=body.config,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job_to_response(job)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="Get job status and latest results",
)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobResponse:
    job = await _get_visible_job(session, job_id, user)
    iterations = await get_iterations(session, job_id)
    latest_results = await get_latest_task_results(session, job_id)
    version_no = await best_agent_version_no(session, job)
    return job_to_response(
        job,
        iterations=iterations,
        latest_task_results=latest_results,
        best_version_no=version_no,
    )


@router.get(
    "/{job_id}/iterations",
    response_model=list[IterationSummary],
    responses={404: {"model": ErrorResponse}},
    summary="List all iterations for a job",
)
async def list_iterations(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[IterationSummary]:
    await _get_visible_job(session, job_id, user)
    iterations = await get_iterations(session, job_id)
    return [IterationSummary.model_validate(i) for i in iterations]


@router.get(
    "/{job_id}/iterations/{iteration_no}",
    response_model=IterationDetail,
    responses={404: {"model": ErrorResponse}},
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
    task_results = await get_task_results_for_iteration(session, iteration.id)
    return iteration_to_detail(iteration, task_results)


@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Cancel a queued or running job",
)
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobResponse:
    job = await _get_visible_job(session, job_id, user)
    if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail=f"Job already {job.status.value}")
    job.status = JobStatus.CANCELLED
    await session.commit()
    await session.refresh(job)
    return job_to_response(job)


async def _get_visible_job(session: AsyncSession, job_id: str, user: User) -> Job:
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != Role.ADMIN and job.created_by != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this job")
    return job
