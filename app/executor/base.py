from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

# pending_ids, running_ids
TaskProgressCallback = Callable[[list[str], list[str]], Awaitable[None]]
CancellationCallback = Callable[[], Awaitable[bool]]


@dataclass
class TaskExecution:
    task_id: str
    reward: float | None
    status: str  # passed | failed | infra_error
    failure_summary: str | None = None
    trace: dict | list | None = None
    verifier_result: dict | None = None


@dataclass
class BenchmarkResult:
    task_results: list[TaskExecution] = field(default_factory=list)
    val_score: float = 0.0
    executor_log: str = ""

    @property
    def all_passed(self) -> bool:
        return bool(self.task_results) and all(t.status == "passed" for t in self.task_results)


class Executor(Protocol):
    async def run_benchmark(
        self,
        task_ids: list[str],
        agent_content: str,
        *,
        on_progress: TaskProgressCallback | None = None,
        should_cancel: CancellationCallback | None = None,
    ) -> BenchmarkResult: ...
