from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.executor import get_executor
from app.executor.base import TaskExecution
from app.harness.template import AGENT_TEMPLATE
from app.models.iteration import Iteration, IterationPhase
from app.models.job import Job, JobStatus, StopReason
from app.optimizer import Optimizer
from app.services.agent_versions import (
    create_agent_version,
    get_agent_version,
    save_iteration_results,
)

logger = logging.getLogger(__name__)

_LEARNING_HISTORY_CHARS = 16000


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
        optimizer_mode = self.optimizer.effective_mode()
        logger.info(
            "[job %s] starting optimization loop: executor=%s optimizer_mode=%s max_iterations=%s patience=%s tasks=%s",
            job.id,
            job.executor,
            optimizer_mode,
            job.max_iterations,
            job.patience,
            len(job.task_ids),
        )

        v0 = await create_agent_version(
            self.session,
            job_id=job.id,
            version_no=0,
            content=AGENT_TEMPLATE,
            parent_version_no=None,
            created_by_iteration=None,
        )
        await self.session.commit()

        best_version_no = 0
        best_score: float | None = None
        best_failures: list = []
        best_task_results: list = []
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
                tasks_pending=len(job.task_ids),
                tasks_running=0,
                tasks_completed=0,
            )
            self.session.add(iteration)
            await self.session.flush()
            await self.session.commit()

            async def on_progress(pending: int, running: int, completed: int) -> None:
                # Fresh session on the same bind so Harbor's thread-bridged
                # callbacks never share the processor session concurrently.
                factory = async_sessionmaker(self.session.bind, expire_on_commit=False, class_=AsyncSession)
                async with factory() as progress_session:
                    result = await progress_session.execute(select(Iteration).where(Iteration.id == iteration.id))
                    row = result.scalar_one_or_none()
                    if row is None:
                        return
                    row.tasks_pending = pending
                    row.tasks_running = running
                    row.tasks_completed = completed
                    await progress_session.commit()

            result = await executor.run_benchmark(
                job.task_ids,
                version.content,
                on_progress=on_progress,
            )
            await self.session.refresh(iteration)
            iteration.tasks_pending = 0
            iteration.tasks_running = 0
            iteration.tasks_completed = len(job.task_ids)
            await save_iteration_results(self.session, iteration=iteration, job=job, benchmark_result=result)
            passed = sum(1 for t in result.task_results if t.status == "passed")
            failed = sum(1 for t in result.task_results if t.status == "failed")
            infra = sum(1 for t in result.task_results if t.status == "infra_error")
            failed_ids = [t.task_id for t in result.task_results if t.status != "passed"]
            logger.info(
                "[job %s] iter %s benchmark done: agent_v=%s passed=%s failed=%s "
                "infra_error=%s val_score=%.3f failed_tasks=%s",
                job.id,
                iteration_no,
                run_version_no,
                passed,
                failed,
                infra,
                result.val_score,
                failed_ids or "none",
            )
            iteration.phase = IterationPhase.DONE
            await self.session.commit()

            recent_attempt = None
            if iteration_no == 0:
                best_score = result.val_score
                job.best_val_score = best_score
                job.best_agent_version_id = v0.id
                best_task_results = list(result.task_results)
                best_failures = [t for t in best_task_results if t.status != "passed"]
                iteration.accepted = True
                await self.session.commit()
                logger.info(
                    "[job %s] iter 0 baseline set: best_val_score=%.3f agent_v=0",
                    job.id,
                    best_score,
                )
            else:
                parent_score = best_score
                parent_task_results = list(best_task_results)
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
                    best_task_results = list(result.task_results)
                    best_failures = [t for t in best_task_results if t.status != "passed"]
                    logger.info(
                        "[job %s] iter %s accepted: new best_val_score=%.3f agent_v=%s",
                        job.id,
                        iteration_no,
                        best_score,
                        best_version_no,
                    )
                else:
                    iteration.accepted = False
                    no_improve += 1
                    logger.info(
                        "[job %s] iter %s rejected: val_score=%.3f (best=%.3f) no_improve=%s/%s",
                        job.id,
                        iteration_no,
                        result.val_score,
                        best_score or 0.0,
                        no_improve,
                        job.patience,
                    )
                recent_attempt = build_recent_attempt(
                    source_version_no=version.parent_version_no,
                    candidate_version_no=run_version_no,
                    diff=version.diff,
                    parent_score=parent_score,
                    candidate_score=result.val_score,
                    accepted=bool(iteration.accepted),
                    parent_tasks=parent_task_results,
                    candidate_tasks=result.task_results,
                )
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
                all_tasks=best_task_results,
                accumulated_learnings=job.learnings,
                iteration_no=iteration_no,
                val_score=best_score or 0.0,
                source_agent_version_no=best_version_no,
                recent_attempt=recent_attempt,
            )

            iteration.llm_finished_at = datetime.now(timezone.utc)
            iteration.llm_prompt = proposal.prompt
            iteration.llm_response = proposal.raw_response
            iteration.improvement_rationale = proposal.rationale
            iteration.learnings = proposal.learnings
            iteration.optimizer_context = {
                "source_agent_version_no": best_version_no,
                "failing_task_count": len(proposal.failure_context),
                "passed_task_count": len(proposal.success_context),
                "infra_error_count": len(proposal.infra_context),
                "failure_context": proposal.failure_context,
                "success_context": proposal.success_context,
                "infra_context": proposal.infra_context,
                "recent_attempt": proposal.recent_attempt,
                "optimizer_mode": proposal.optimizer_mode,
                "configured_optimizer_mode": get_settings().optimizer_mode,
                "optimizer_model": get_settings().optimizer_model,
                "agent_model": get_settings().agent_model,
                "prompt_chars": len(proposal.prompt),
                "fallback_reason": proposal.fallback_reason,
            }
            job.learnings = append_learning_history(
                job.learnings,
                iteration_no=iteration_no,
                source_agent_version_no=best_version_no,
                rationale=proposal.rationale,
                learnings=proposal.learnings,
            )
            await self.session.commit()

            if proposal.agent_content == best_version.content:
                job.stop_reason = StopReason.NO_IMPROVEMENT
                iteration.phase = IterationPhase.DONE
                logger.info(
                    "[job %s] iter %s optimizer returned no agent change; stopping",
                    job.id,
                    iteration_no,
                )
                await self.session.commit()
                return

            new_version = await create_agent_version(
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
            diff_preview = (new_version.diff or "").strip()
            if diff_preview:
                diff_lines = diff_preview.splitlines()
                if len(diff_lines) > 24:
                    diff_preview = "\n".join(diff_lines[:24] + ["... (truncated)"])
            else:
                diff_preview = "(no diff — agent unchanged)"
            logger.info(
                "[job %s] iter %s proposal ready: source_agent_v=%s -> agent_v=%s failing_tasks=%s rationale=%s\n%s",
                job.id,
                iteration_no,
                best_version_no,
                candidate_version_no,
                [t.task_id for t in best_failures],
                (proposal.rationale or "")[:200],
                diff_preview,
            )
            for failure in proposal.failure_context[:4]:
                logger.info(
                    "[job %s] iter %s optimizer saw task=%s status=%s summary=%s trace=%s",
                    job.id,
                    iteration_no,
                    failure.get("task_id"),
                    failure.get("status"),
                    (failure.get("failure_summary") or "")[:120],
                    str(failure.get("trace") or "")[:240],
                )

        job.stop_reason = StopReason.MAX_ITERATIONS

    async def _load_job(self, job_id: str) -> Job | None:
        result = await self.session.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()


def build_recent_attempt(
    *,
    source_version_no: int | None,
    candidate_version_no: int,
    diff: str | None,
    parent_score: float | None,
    candidate_score: float,
    accepted: bool,
    parent_tasks: list[TaskExecution],
    candidate_tasks: list[TaskExecution],
) -> dict:
    parent_by_id = {task.task_id: task for task in parent_tasks}
    task_deltas = []
    for candidate in candidate_tasks:
        parent = parent_by_id.get(candidate.task_id)
        before_reward = parent.reward if parent else None
        reward_delta = None
        if before_reward is not None and candidate.reward is not None:
            reward_delta = candidate.reward - before_reward
        task_deltas.append(
            {
                "task_id": candidate.task_id,
                "before_status": parent.status if parent else None,
                "after_status": candidate.status,
                "before_reward": before_reward,
                "after_reward": candidate.reward,
                "reward_delta": reward_delta,
            }
        )

    score_delta = candidate_score - parent_score if parent_score is not None else None
    return {
        "source_agent_version_no": source_version_no,
        "candidate_agent_version_no": candidate_version_no,
        "accepted": accepted,
        "parent_score": parent_score,
        "candidate_score": candidate_score,
        "score_delta": score_delta,
        "diff": diff or "",
        "task_deltas": task_deltas,
    }


def append_learning_history(
    existing: str,
    *,
    iteration_no: int,
    source_agent_version_no: int,
    rationale: str,
    learnings: str,
) -> str:
    entry = (
        f"## Proposal after iteration {iteration_no} (source agent v{source_agent_version_no})\n"
        f"Rationale: {rationale.strip() or '(none)'}\n"
        f"Learnings: {learnings.strip() or '(none)'}"
    )
    combined = f"{existing.rstrip()}\n\n{entry}".strip() if existing else entry
    if len(combined) <= _LEARNING_HISTORY_CHARS:
        return combined
    marker = "… earlier proposal history omitted …\n\n"
    return marker + combined[-(_LEARNING_HISTORY_CHARS - len(marker)) :]
