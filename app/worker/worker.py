from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, update

from app.config import get_settings
from app.db import session_factory
from app.models.job import Job, JobStatus
from app.worker.processor import JobProcessor

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("Background worker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        logger.info("Background worker stopped")

    async def _loop(self) -> None:
        settings = get_settings()
        while not self._stop.is_set():
            job_id = await self._claim_next_job()
            if job_id is None:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=settings.worker_poll_interval_sec,
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            logger.info("Processing job %s", job_id)
            async with session_factory()() as session:
                processor = JobProcessor(session)
                try:
                    await processor.process(job_id)
                except Exception:
                    logger.exception("Job %s failed", job_id)

    async def _claim_next_job(self) -> str | None:
        async with session_factory()() as session:
            result = await session.execute(
                select(Job)
                .where(Job.status == JobStatus.QUEUED)
                .order_by(Job.created_at)
                .limit(1)
            )
            job = result.scalar_one_or_none()
            if job is None:
                return None

            await session.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.QUEUED)
                .values(status=JobStatus.RUNNING)
            )
            await session.commit()
            return job.id
