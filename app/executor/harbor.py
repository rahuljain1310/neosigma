from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from app.config import get_settings
from app.executor.base import BenchmarkResult, Executor, TaskExecution
from app.harness.workspace import HarnessWorkspace, make_workspace


class HarborExecutor(Executor):
    """Run Terminal-Bench via auto-harness's TerminalBenchRunner (harbor run).

    Each task executes in an isolated sandbox (docker/e2b/daytona) managed by Harbor.
    The agent LLM loop runs in the Harbor process; only bash commands enter the sandbox.
    """

    def __init__(self, job_id: str, config: dict | None = None):
        self.job_id = job_id
        self.config = config or {}
        self.workspace = make_workspace(job_id)

    async def run_benchmark(self, task_ids: list[str], agent_content: str) -> BenchmarkResult:
        return await asyncio.to_thread(self._run_sync, task_ids, agent_content)

    def _run_sync(self, task_ids: list[str], agent_content: str) -> BenchmarkResult:
        settings = get_settings()
        self.workspace.ensure_repo()
        self.workspace.write_agent(agent_content)
        self._write_experiment_config(task_ids, settings)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.workspace.root) + os.pathsep + env.get("PYTHONPATH", "")
        env["AGENT_MODEL"] = self.config.get("agent_model", settings.agent_model)
        env["HARNESS_SAVE_TRACE"] = "1"

        cmd = [
            sys.executable,
            "-c",
            _RUNNER_SNIPPET,
            str(self.workspace.root),
            json.dumps(task_ids),
            self.config.get("env_provider", settings.harbor_env_provider),
            str(self.config.get("n_concurrent", settings.harbor_n_concurrent)),
            self.config.get("agent_model", settings.agent_model),
            str(self.config.get("per_task_timeout", settings.per_task_timeout_sec)),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(600, len(task_ids) * 300),
                env=env,
            )
            log = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
            payload = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except subprocess.TimeoutExpired:
            return BenchmarkResult(
                task_results=[
                    TaskExecution(
                        task_id=tid,
                        reward=None,
                        status="infra_error",
                        failure_summary="Harbor subprocess timed out",
                    )
                    for tid in task_ids
                ],
                val_score=0.0,
                executor_log="Harbor subprocess timed out",
            )
        except Exception as e:
            return BenchmarkResult(
                task_results=[
                    TaskExecution(
                        task_id=tid,
                        reward=None,
                        status="infra_error",
                        failure_summary=str(e),
                    )
                    for tid in task_ids
                ],
                val_score=0.0,
                executor_log=str(e),
            )

        results_map: dict[str, float | None] = payload.get("results", {})
        task_results: list[TaskExecution] = []
        for task_id in task_ids:
            reward = results_map.get(task_id)
            trace, verifier = self._load_artifacts(task_id)
            if reward is None:
                status = "infra_error"
                summary = "No verifier result — sandbox or agent timeout"
            elif reward >= 0.5:
                status = "passed"
                summary = None
            else:
                status = "failed"
                summary = f"Task {task_id} failed with reward {reward:.2f}"

            task_results.append(
                TaskExecution(
                    task_id=task_id,
                    reward=reward,
                    status=status,
                    failure_summary=summary,
                    trace=trace,
                    verifier_result=verifier,
                )
            )

        val = sum((t.reward or 0.0) for t in task_results) / max(len(task_results), 1)
        return BenchmarkResult(task_results=task_results, val_score=val, executor_log=log)

    def _write_experiment_config(self, task_ids: list[str], settings) -> None:
        import yaml

        split_file = self.workspace.root / "tbench_data" / "task_split.json"
        split_file.parent.mkdir(parents=True, exist_ok=True)
        split_file.write_text(json.dumps({"train": task_ids, "test": task_ids}))

        cfg = {
            "benchmark": "terminal-bench",
            "agent_model": self.config.get("agent_model", settings.agent_model),
            "split": "train",
            "gate_split": "test",
            "env_provider": self.config.get("env_provider", settings.harbor_env_provider),
            "max_concurrency": self.config.get("n_concurrent", settings.harbor_n_concurrent),
            "per_task_timeout": self.config.get("per_task_timeout", settings.per_task_timeout_sec),
        }
        (self.workspace.root / "experiment_config.yaml").write_text(yaml.dump(cfg))

    def _load_artifacts(self, task_id: str) -> tuple[dict | list | None, dict | None]:
        base = self.workspace.traces_dir / task_id
        trace = None
        verifier = None
        trace_path = base / "trace.json"
        result_path = base / "result.json"
        if trace_path.exists():
            trace = json.loads(trace_path.read_text())
        if result_path.exists():
            verifier = json.loads(result_path.read_text())
        return trace, verifier


_RUNNER_SNIPPET = r'''
import json, os, sys
from benchmark import TerminalBenchRunner

root, task_ids_json, env_provider, n_concurrent, agent_model, per_task_timeout = sys.argv[1:7]
os.chdir(root)
task_ids = json.loads(task_ids_json)
runner = TerminalBenchRunner(
    agent_model=agent_model,
    split="train",
    env_provider=env_provider,
    n_concurrent=int(n_concurrent),
    per_task_timeout=int(per_task_timeout),
    jobs_dir="workspace/tbench_jobs",
)
results = runner.run(task_ids=task_ids)
val = runner.val_score(results)
print(json.dumps({"results": results, "val_score": val}))
'''
