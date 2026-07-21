#!/usr/bin/env python3
"""End-to-end client for the Agent Optimization Service."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.auth import DEFAULT_ORG_NAME, DEFAULT_USER_EMAIL, DEFAULT_USER_PASSWORD
from client_debug import (
    DEBUG_ARTIFACTS_DIR,
    dump_iteration_debug,
    fetch_iteration_debug_bundle,
    print_iteration_debug,
)

DEFAULT_BASE = os.environ.get("AOS_BASE_URL", "http://localhost:8000")
_TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class JobCleanup:
    base_url: str
    job_id: str
    headers: dict[str, str]
    armed: bool = True

    def disarm(self) -> None:
        self.armed = False

    def cancel(self) -> None:
        """Best-effort cleanup used when the client exits before the job does."""
        if not self.armed:
            return
        self.armed = False
        try:
            with httpx.Client(base_url=self.base_url, timeout=15.0) as client:
                response = client.post(f"/jobs/{self.job_id}/cancel", headers=self.headers)
                if response.status_code == 409:
                    print(f"\nJob {self.job_id} already reached a terminal state.")
                    return
                response.raise_for_status()
                print(f"\nCancelled job {self.job_id} because the client stopped tracking it.")
        except Exception as exc:
            print(f"\nWarning: could not cancel job {self.job_id}: {exc}")


def _raise_keyboard_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def login(client: httpx.Client, org_name: str, email: str, password: str) -> str:
    resp = client.post(
        "/auth/login",
        json={"org_name": org_name, "email": email, "password": password},
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"]


def _tasks_summary(it: dict[str, Any]) -> dict[str, list[str]]:
    summary = it.get("tasks_summary") or {}
    return {
        "pending": list(summary.get("pending") or []),
        "running": list(summary.get("running") or []),
        "completed": list(summary.get("completed") or []),
        "passed": list(summary.get("passed") or []),
        "failed": list(summary.get("failed") or []),
        "infra_error": list(summary.get("infra_error") or []),
    }


def _benchmark_snapshot(it: dict[str, Any]) -> tuple[Any, ...]:
    ts = _tasks_summary(it)
    return (
        it.get("val_score"),
        tuple(ts["passed"]),
        tuple(ts["failed"]),
        tuple(ts["infra_error"]),
        it.get("accepted"),
    )


def _proposal_snapshot(it: dict[str, Any]) -> tuple[Any, ...]:
    return (
        it.get("proposed_agent_version_no"),
        it.get("improvement_rationale"),
    )


def _progress_snapshot(it: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    ts = _tasks_summary(it)
    return tuple(ts["pending"]), tuple(ts["running"]), tuple(ts["completed"])


def _format_task_counts(it: dict[str, Any]) -> str:
    ts = _tasks_summary(it)
    passed = len(ts["passed"])
    failed = len(ts["failed"])
    infra = len(ts["infra_error"])
    total = passed + failed + infra
    return f"{passed}/{total} passed, {failed} failed, {infra} infra_error"


def _format_progress(pending: int, running: int, completed: int) -> str:
    total = pending + running + completed
    return f"tasks: {pending} pending, {running} running, {completed} completed ({completed}/{total})"


def _print_benchmark_progress(it: dict[str, Any], *, first: bool) -> None:
    iteration_no = it["iteration_no"]
    agent_v = it.get("agent_version_no")
    pending, running, completed = _progress_snapshot(it)
    progress = _format_progress(len(pending), len(running), len(completed))
    if first:
        print(f"  [iter {iteration_no}] running benchmark with agent_v={agent_v} | {progress}")
    else:
        print(f"  [iter {iteration_no}] {progress}")


def _print_benchmark_done(it: dict[str, Any], *, best_val_score: float | None) -> None:
    iteration_no = it["iteration_no"]
    agent_v = it.get("agent_version_no")
    val_score = it.get("val_score")
    counts = _format_task_counts(it)
    failed_ids = _tasks_summary(it)["failed"] + _tasks_summary(it)["infra_error"]
    failed_note = f" ({', '.join(failed_ids)})" if failed_ids else ""
    print(
        f"  [iter {iteration_no}] benchmark done: agent_v={agent_v} val_score={val_score:.3f} | {counts}{failed_note}"
    )

    if it.get("accepted") is True and iteration_no > 0:
        print(f"  [iter {iteration_no}] accepted — new best_val_score={best_val_score:.3f} (agent_v={agent_v})")
    elif it.get("accepted") is False:
        best_note = f"{best_val_score:.3f}" if best_val_score is not None else "n/a"
        print(f"  [iter {iteration_no}] rejected — val_score={val_score:.3f} (best remains {best_note})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Optimization Service test client")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--org", default=os.environ.get("AOS_ORG", DEFAULT_ORG_NAME))
    parser.add_argument("--email", default=os.environ.get("AOS_EMAIL", DEFAULT_USER_EMAIL))
    parser.add_argument(
        "--password",
        default=os.environ.get("AOS_PASSWORD", DEFAULT_USER_PASSWORD),
    )
    parser.add_argument("--executor", choices=["simulated", "harbor"], default="harbor")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--task-ids", nargs="*", default=None)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print optimizer traces and agent.py diffs after each proposal",
    )
    parser.add_argument(
        "--dump-dir",
        type=Path,
        default=None,
        help="Write per-iteration debug artifacts (default: .debug/<job_id> when --debug)",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        health = client.get("/health")
        health.raise_for_status()
        print("Health:", health.json())

        access_token = login(client, args.org, args.email, args.password)
        headers = {"Authorization": f"Bearer {access_token}"}
        print(f"Logged in as {args.email} @ {args.org}")

        payload: dict[str, Any] = {
            "max_iterations": args.max_iterations,
            "patience": args.patience,
            "executor": args.executor,
            "config": {},
        }
        if args.task_ids:
            payload["task_ids"] = args.task_ids

        created = client.post("/jobs", json=payload, headers=headers)
        created.raise_for_status()
        job = created.json()
        job_id = job["id"]
        print(f"Submitted job {job_id} (status={job['status']})")
        cleanup = JobCleanup(args.base_url, job_id, headers)
        atexit.register(cleanup.cancel)

        seen_benchmarks: dict[int, tuple[Any, ...]] = {}
        seen_proposals: dict[int, tuple[Any, ...]] = {}
        seen_progress: dict[int, tuple[int, int, int]] = {}
        seen_running: set[int] = set()
        seen_debug: set[int] = set()
        last_status: str | None = None
        dump_dir = args.dump_dir
        if args.debug and dump_dir is None:
            dump_dir = DEBUG_ARTIFACTS_DIR / job_id

        while True:
            resp = client.get(f"/jobs/{job_id}", headers=headers)
            resp.raise_for_status()
            job = resp.json()
            status = job["status"]
            best_val_score = job.get("best_val_score")

            if status != last_status:
                score_note = (
                    f"best_val_score={best_val_score:.3f}" if best_val_score is not None else "best_val_score=None"
                )
                print(f"  job status={status} {score_note}")
                last_status = status

            for it in job.get("iterations", []):
                iteration_no = it["iteration_no"]
                phase = it.get("phase")

                if phase in {"running_benchmark", "pending"}:
                    progress = _progress_snapshot(it)
                    first = iteration_no not in seen_running
                    if first or seen_progress.get(iteration_no) != progress:
                        _print_benchmark_progress(it, first=first)
                        seen_running.add(iteration_no)
                        seen_progress[iteration_no] = progress

                bench_key = _benchmark_snapshot(it)
                if it.get("val_score") is not None and seen_benchmarks.get(iteration_no) != bench_key:
                    _print_benchmark_done(it, best_val_score=best_val_score)
                    seen_benchmarks[iteration_no] = bench_key
                    seen_progress[iteration_no] = _progress_snapshot(it)

                proposal_key = _proposal_snapshot(it)
                if (
                    it.get("proposed_agent_version_no") is not None
                    and it.get("improvement_rationale")
                    and seen_proposals.get(iteration_no) != proposal_key
                ):
                    rationale = it["improvement_rationale"].strip()
                    if len(rationale) > 160:
                        rationale = rationale[:157] + "..."
                    print(
                        f"  [iter {iteration_no}] proposed agent_v="
                        f"{it['proposed_agent_version_no']} for next run: {rationale}"
                    )
                    seen_proposals[iteration_no] = proposal_key

                if (
                    args.debug
                    and it.get("proposed_agent_version_no") is not None
                    and it.get("optimizer_finished_at")
                    and iteration_no not in seen_debug
                ):
                    detail, proposed_version = fetch_iteration_debug_bundle(client, job_id, iteration_no, headers)
                    print_iteration_debug(
                        iteration_no=iteration_no,
                        detail=detail,
                        proposed_version=proposed_version,
                    )
                    if dump_dir:
                        dump_iteration_debug(
                            dump_dir=dump_dir,
                            iteration_no=iteration_no,
                            detail=detail,
                            proposed_version=proposed_version,
                        )
                    seen_debug.add(iteration_no)

            if status in _TERMINAL_JOB_STATUSES:
                cleanup.disarm()
                break
            time.sleep(args.poll_interval)

        print("\n=== Job summary ===")
        print(
            json.dumps(
                {
                    "id": job["id"],
                    "status": job["status"],
                    "stop_reason": job.get("stop_reason"),
                    "best_val_score": job.get("best_val_score"),
                    "best_agent_version_no": job.get("best_agent_version_no"),
                    "task_ids": job.get("task_ids"),
                },
                indent=2,
            )
        )

        print("\n=== Latest task results ===")
        for tr in job.get("latest_task_results", []):
            print(f"  {tr['task_id']}: {tr['status']} reward={tr.get('reward')}")

        iterations = job.get("iterations", [])
        print(f"\n=== Iteration history ({len(iterations)} iterations) ===")
        for it in iterations:
            accepted = it.get("accepted")
            mark = "✓" if accepted else ("✗" if accepted is False else "-")
            counts = _format_task_counts(it)
            print(
                f"  [{mark}] iter={it['iteration_no']} "
                f"phase={it['phase']} val_score={it.get('val_score')} "
                f"agent_v={it['agent_version_no']} | {counts}"
            )
            if _tasks_summary(it)["failed"] or _tasks_summary(it)["infra_error"]:
                failed = _tasks_summary(it)["failed"] + _tasks_summary(it)["infra_error"]
                print(f"       failed: {', '.join(failed)}")
            if it.get("improvement_rationale"):
                print(f"       changes: {it['improvement_rationale'][:160]}")

        if args.debug and dump_dir:
            print(f"\nDebug artifacts in {dump_dir.resolve()}")

    return 0


def cli() -> int:
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        return main()
    except KeyboardInterrupt:
        print("\nInterrupted; cancelling the active job before exit.")
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(cli())
