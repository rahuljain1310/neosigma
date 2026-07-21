from app.executor.base import BenchmarkResult, Executor
from app.executor.harbor import HarborExecutor
from app.executor.simulated import SimulatedExecutor


def get_executor(job_id: str, executor_name: str, config: dict) -> Executor:
    if executor_name == "harbor":
        return HarborExecutor(job_id=job_id, config=config)
    return SimulatedExecutor()
