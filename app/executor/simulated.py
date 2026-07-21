from __future__ import annotations

import hashlib
import json

from app.executor.base import BenchmarkResult, Executor, TaskExecution


class SimulatedExecutor(Executor):
    """Deterministic fake benchmark for M1/M2/M4 without sandbox cost.

    Reward model (per task):
    - Base score depends on a stable hash of task_id (0.0 or 1.0).
    - Agent improvements in AGENT_INSTRUCTION boost the pass rate:
      * "TODO" planning keyword -> +1 task
      * "non-interactive" keyword -> +1 task
      * "verify" emphasis -> +1 task
    This gives the optimizer loop something real to optimize against offline.
    """

    async def run_benchmark(self, task_ids: list[str], agent_content: str) -> BenchmarkResult:
        boosts = 0
        lower = agent_content.lower()
        if "todo" in lower or "plan" in lower:
            boosts += 1
        if "non-interactive" in lower or "do not ask" in lower:
            boosts += 1
        if "verify" in lower and "before finishing" in lower:
            boosts += 1

        task_results: list[TaskExecution] = []
        for i, task_id in enumerate(task_ids):
            base_pass = _stable_pass(task_id)
            effective_pass = base_pass or (i < boosts)

            if effective_pass:
                reward = 1.0
                status = "passed"
                summary = None
            else:
                reward = 0.0
                status = "failed"
                summary = (
                    f"Simulated failure on {task_id}: agent did not explore environment "
                    f"or verify solution. Current boosts applied: {boosts}."
                )

            trace = _fake_trace(task_id, effective_pass, agent_content)
            task_results.append(
                TaskExecution(
                    task_id=task_id,
                    reward=reward,
                    status=status,
                    failure_summary=summary,
                    trace=trace,
                    verifier_result={"task_name": task_id, "verifier_result": {"rewards": {"reward": reward}}},
                )
            )

        val = sum(t.reward or 0.0 for t in task_results) / max(len(task_results), 1)
        log = json.dumps(
            {
                "executor": "simulated",
                "boosts": boosts,
                "task_ids": task_ids,
                "val_score": val,
            },
            indent=2,
        )
        return BenchmarkResult(task_results=task_results, val_score=val, executor_log=log)


def _stable_pass(task_id: str) -> bool:
    # ~40% of tasks pass with the bare template.
    h = int(hashlib.sha256(task_id.encode()).hexdigest(), 16)
    return h % 5 < 2


def _fake_trace(task_id: str, passed: bool, agent_content: str) -> list[dict]:
    snippet = agent_content[agent_content.find("AGENT_INSTRUCTION"):][:400]
    return [
        {"role": "system", "content": snippet},
        {"role": "user", "content": f"Task: {task_id}"},
        {
            "role": "assistant",
            "content": "Explored environment and attempted solution."
            if passed
            else "Gave up early without verifying.",
        },
    ]
