from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys

from app.config import get_settings
from app.executor.base import BenchmarkResult, Executor, TaskExecution
from app.harness.workspace import HarnessWorkspace, make_workspace

logger = logging.getLogger(__name__)

_ENV_PROVIDER = "daytona"


class HarborExecutor(Executor):
    """Run Terminal-Bench via auto-harness's TerminalBenchRunner (harbor run).

    Each task executes in an isolated Daytona sandbox managed by Harbor.
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
        preflight_error = self._preflight(settings)
        if preflight_error:
            return self._infra_failure(task_ids, preflight_error)

        self.workspace.ensure_repo()
        self.workspace.write_agent(agent_content)
        self._write_experiment_config(task_ids, settings)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.workspace.root) + os.pathsep + env.get("PYTHONPATH", "")
        env["AGENT_MODEL"] = self.config.get("agent_model", settings.agent_model)
        env["HARNESS_SAVE_TRACE"] = "1"
        if settings.openai_api_key and not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = settings.openai_api_key
        if settings.daytona_api_key and not env.get("DAYTONA_API_KEY"):
            env["DAYTONA_API_KEY"] = settings.daytona_api_key

        cmd = [
            sys.executable,
            "-c",
            _RUNNER_SNIPPET,
            str(self.workspace.root),
            json.dumps(task_ids),
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
        except subprocess.TimeoutExpired:
            return self._infra_failure(task_ids, "Harbor subprocess timed out")
        except Exception as e:
            return self._infra_failure(task_ids, f"Harbor subprocess failed to start: {e}")

        log = _combine_logs(proc.stdout, proc.stderr)
        payload, parse_error = _extract_payload(proc.stdout)
        if parse_error is not None:
            summary = f"Harbor runner did not return JSON results (exit={proc.returncode}). {parse_error}"
            logger.error("[harbor job=%s] %s\n%s", self.job_id, summary, log[-4000:])
            return self._infra_failure(task_ids, summary, executor_log=log)

        if proc.returncode != 0 and not payload.get("results"):
            summary = f"Harbor runner exited {proc.returncode}"
            logger.error("[harbor job=%s] %s\n%s", self.job_id, summary, log[-4000:])
            return self._infra_failure(task_ids, summary, executor_log=log)

        results_map: dict[str, float | None] = payload.get("results", {})
        task_results: list[TaskExecution] = []
        global_error = payload.get("error")
        for task_id in task_ids:
            reward = results_map.get(task_id)
            trace, verifier = self._load_artifacts(task_id)
            exception = _exception_from_artifacts(verifier, self.workspace, task_id)
            if reward is None:
                status = "infra_error"
                summary = (
                    exception
                    or (str(global_error) if global_error else None)
                    or _infer_infra_summary(log)
                    or "No verifier result — sandbox or agent timeout"
                )
            elif reward >= 0.5:
                status = "passed"
                summary = None
            else:
                status = "failed"
                summary = exception or f"Task {task_id} failed with reward {reward:.2f}"

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

    def _preflight(self, settings) -> str | None:
        if shutil.which("harbor") is None:
            return (
                "harbor CLI not found on PATH. Rebuild the API image after installing "
                "the harbor package (pip install 'harbor[daytona]')."
            )
        if not (settings.daytona_api_key or os.environ.get("DAYTONA_API_KEY")):
            return "DAYTONA_API_KEY is required for Harbor+Daytona runs"
        try:
            import daytona  # noqa: F401
        except ImportError:
            return "Harbor daytona extra missing. Rebuild API image with pip install 'harbor[daytona]'."
        return None

    def _infra_failure(
        self,
        task_ids: list[str],
        summary: str,
        *,
        executor_log: str | None = None,
    ) -> BenchmarkResult:
        return BenchmarkResult(
            task_results=[
                TaskExecution(
                    task_id=tid,
                    reward=None,
                    status="infra_error",
                    failure_summary=summary,
                )
                for tid in task_ids
            ],
            val_score=0.0,
            executor_log=executor_log or summary,
        )

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
            "env_provider": _ENV_PROVIDER,
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


def _exception_from_artifacts(
    verifier: dict | list | None,
    workspace: HarnessWorkspace,
    task_id: str,
) -> str | None:
    """Pull Harbor exception text from result.json / exception.txt when present."""
    if isinstance(verifier, dict):
        info = verifier.get("exception_info")
        if isinstance(info, dict):
            msg = info.get("exception_message") or info.get("exception_type")
            if msg:
                return str(msg)[:800]
    base = workspace.traces_dir / task_id
    for name in ("exception.txt", "result.json"):
        path = base / name
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        if name == "exception.txt" and text.strip():
            return text.strip()[:800]
        if name == "result.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            info = data.get("exception_info") if isinstance(data, dict) else None
            if isinstance(info, dict):
                msg = info.get("exception_message") or info.get("exception_type")
                if msg:
                    return str(msg)[:800]
    return None


def _infer_infra_summary(log: str) -> str | None:
    markers = (
        "MissingExtraError",
        "pip install 'harbor[daytona]'",
        "DAYTONA_API_KEY",
        "AuthenticationError",
        "insufficient_quota",
        "RateLimitError",
    )
    for marker in markers:
        if marker in log:
            idx = log.find(marker)
            start = max(0, idx - 80)
            end = min(len(log), idx + 220)
            return " ".join(log[start:end].split())
    return None


def _combine_logs(stdout: str | None, stderr: str | None) -> str:
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    return "\n".join(parts).strip()


def _extract_payload(stdout: str | None) -> tuple[dict, str | None]:
    """Parse the final JSON object emitted by the runner snippet.

    auto-harness prints human logs before the result line; only the last
    JSON object line is the payload.
    """
    if not stdout or not stdout.strip():
        return {}, "empty stdout from harbor runner"
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "results" in data:
            return data, None
    preview = stdout.strip().splitlines()[-1][:240]
    return {}, f"no JSON results line found; last stdout line was: {preview!r}"


_RUNNER_SNIPPET = r"""
import json, os, sys, traceback
from benchmark import TerminalBenchRunner

root, task_ids_json, n_concurrent, agent_model, per_task_timeout = sys.argv[1:6]
os.chdir(root)
task_ids = json.loads(task_ids_json)
try:
    runner = TerminalBenchRunner(
        agent_model=agent_model,
        split="train",
        env_provider="daytona",
        n_concurrent=int(n_concurrent),
        per_task_timeout=int(per_task_timeout),
        jobs_dir="workspace/tbench_jobs",
    )
    results = runner.run(task_ids=task_ids)
    if results and all(v is None for v in results.values()):
        print(
            "[benchmark] WARNING: all task rewards are null — harbor likely failed "
            "before verifiers ran (check stderr for MissingExtraError / auth errors)",
            file=sys.stderr,
        )
    val = runner.val_score(results)
    print(json.dumps({"results": results, "val_score": val}))
except Exception as e:
    traceback.print_exc()
    print(json.dumps({"results": {tid: None for tid in task_ids}, "val_score": 0.0, "error": str(e)}))
    sys.exit(1)
"""
