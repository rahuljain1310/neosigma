from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.executor import get_executor
from app.harness.template import AGENT_TEMPLATE
from app.models.iteration import Iteration, IterationPhase
from app.models.job import Job, JobStatus, StopReason
from app.optimizer import Optimizer
from app.services.agent_versions import create_agent_version, get_agent_version, save_iteration_results


class JobProcessor:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.optimizer = Optimizer()

    async def process(self, job_id: str) -> None:
        job = await self._load_job(job_id)
        if job is None:
            return

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        await self.session.commit()

        try:
            await self._run_loop(job)
            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.COMPLETED
                job.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
        except Exception as e:
            job.status = JobStatus.FAILED
            job.stop_reason = StopReason.ERROR
            job.error = str(e)
            job.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
            raise

    async def _run_loop(self, job: Job) -> None:
        executor = get_executor(job.id, job.executor, job.config)

        v0 = await create_agent_version(
            self.session,
            job_id=job.id,
            version_no=0,
            content=AGENT_TEMPLATE,
            parent_version_no=None,
            created_by_iteration=0,
        )
        await self.session.commit()

        best_version_no = 0
        best_score: float | None = None
        best_failures: list = []
        no_improve = 0
        next_version_no = 1
        candidate_version_no: int | None = None

        for iteration_no in range(job.max_iterations + 1):
            if job.status == JobStatus.CANCELLED:
                job.stop_reason = StopReason.CANCELLED
                return

            run_version_no = 0 if iteration_no == 0 else candidate_version_no
            assert run_version_no is not None

            version = await get_agent_version(self.session, job.id, run_version_no)
            assert version is not None

            iteration = Iteration(
                job_id=job.id,
                iteration_no=iteration_no,
                agent_version_no=run_version_no,
                phase=IterationPhase.RUNNING_BENCHMARK,
                bench_started_at=datetime.now(timezone.utc),
                accepted=None if iteration_no > 0 else True,
            )
            self.session.add(iteration)
            await self.session.flush()

            result = await executor.run_benchmark(job.task_ids, version.content)
            await save_iteration_results(self.session, iteration=iteration, job=job, benchmark_result=result)
            iteration.phase = IterationPhase.DONE
            await self.session.commit()

            if iteration_no == 0:
                best_score = result.val_score
                job.best_val_score = best_score
                job.best_agent_version_id = v0.id
                best_failures = [t for t in result.task_results if t.status != "passed"]
                iteration.accepted = True
                await self.session.commit()
            else:
                improved = best_score is None or result.val_score > best_score
                if improved:
                    best_score = result.val_score
                    best_version_no = run_version_no
                    job.best_val_score = best_score
                    av = await get_agent_version(self.session, job.id, best_version_no)
                    if av:
                        job.best_agent_version_id = av.id
                    iteration.accepted = True
                    no_improve = 0
                    best_failures = [t for t in result.task_results if t.status != "passed"]
                else:
                    iteration.accepted = False
                    no_improve += 1
                await self.session.commit()

                if no_improve >= job.patience:
                    job.stop_reason = StopReason.NO_IMPROVEMENT
                    return

            if result.all_passed:
                job.stop_reason = StopReason.ALL_TASKS_PASSED
                return

            if iteration_no >= job.max_iterations:
                job.stop_reason = StopReason.MAX_ITERATIONS
                return

            best_version = await get_agent_version(self.session, job.id, best_version_no)
            assert best_version is not None

            iteration.phase = IterationPhase.PROPOSING
            iteration.llm_started_at = datetime.now(timezone.utc)
            await self.session.commit()

            proposal = await self.optimizer.propose(
                current_agent=best_version.content,
                failing_tasks=best_failures,
                accumulated_learnings=job.learnings,
                iteration_no=iteration_no,
                val_score=best_score or 0.0,
            )

            iteration.llm_finished_at = datetime.now(timezone.utc)
            iteration.llm_prompt = proposal.prompt
            iteration.llm_response = proposal.raw_response
            iteration.improvement_rationale = proposal.rationale
            iteration.learnings = proposal.learnings
            job.learnings = proposal.learnings
            await self.session.commit()

            await create_agent_version(
                self.session,
                job_id=job.id,
                version_no=next_version_no,
                content=proposal.agent_content,
                parent_version_no=best_version_no,
                parent_content=best_version.content,
                created_by_iteration=iteration_no,
            )
            candidate_version_no = next_version_no
            next_version_no += 1
            iteration.phase = IterationPhase.DONE
            await self.session.commit()

        job.stop_reason = StopReason.MAX_ITERATIONS

    async def _load_job(self, job_id: str) -> Job | None:
        result = await self.session.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()
