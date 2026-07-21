#!/usr/bin/env python3
"""End-to-end client for the Agent Optimization Service."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

import httpx

from app.auth import DEFAULT_ORG_NAME, DEFAULT_USER_EMAIL, DEFAULT_USER_PASSWORD

DEFAULT_BASE = os.environ.get("AOS_BASE_URL", "http://localhost:8000")


def login(client: httpx.Client, org_name: str, email: str, password: str) -> str:
    resp = client.post(
        "/auth/login",
        json={"org_name": org_name, "email": email, "password": password},
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"]



def _benchmark_snapshot(it: dict[str, Any]) -> tuple[Any, ...]:
    return (
        it.get("val_score"),
        it.get("tasks_passed"),
        it.get("tasks_failed"),
        it.get("tasks_infra_error"),
        tuple(it.get("failed_task_ids") or []),
        it.get("accepted"),
    )


def _proposal_snapshot(it: dict[str, Any]) -> tuple[Any, ...]:
    return (
        it.get("proposed_agent_version_no"),
        it.get("improvement_rationale"),
    )


def _format_task_counts(it: dict[str, Any]) -> str:
    passed = it.get("tasks_passed", 0)
    failed = it.get("tasks_failed", 0)
    infra = it.get("tasks_infra_error", 0)
    total = passed + failed + infra
    return f"{passed}/{total} passed, {failed} failed, {infra} infra_error"


def _print_iteration_update(it: dict[str, Any], *, best_val_score: float | None) -> None:
    iteration_no = it["iteration_no"]
    phase = it.get("phase")
    agent_v = it.get("agent_version_no")
    val_score = it.get("val_score")

    if phase in {"running_benchmark", "pending"}:
        print(f"  [iter {iteration_no}] running benchmark with agent_v={agent_v} ...")
        return

    if val_score is not None and phase in {
        "analyzing",
        "proposing",
        "done",
        "failed",
    }:
        counts = _format_task_counts(it)
        failed_ids = it.get("failed_task_ids") or []
        failed_note = f" ({', '.join(failed_ids)})" if failed_ids else ""
        print(
            f"  [iter {iteration_no}] benchmark done: agent_v={agent_v} "
            f"val_score={val_score:.3f} | {counts}{failed_note}"
        )

    if it.get("accepted") is True and iteration_no > 0:
        print(
            f"  [iter {iteration_no}] accepted — new best_val_score={best_val_score:.3f} "
            f"(agent_v={agent_v})"
        )
    elif it.get("accepted") is False:
        best_note = f"{best_val_score:.3f}" if best_val_score is not None else "n/a"
        print(
            f"  [iter {iteration_no}] rejected — val_score={val_score:.3f} "
            f"(best remains {best_note})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Optimization Service test client")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--org", default=os.environ.get("AOS_ORG", DEFAULT_ORG_NAME))
    parser.add_argument("--email", default=os.environ.get("AOS_EMAIL", DEFAULT_USER_EMAIL))
    parser.add_argument(
        "--password",
        default=os.environ.get("AOS_PASSWORD", DEFAULT_USER_PASSWORD),
    )
    parser.add_argument("--executor", choices=["simulated", "harbor"], default="simulated")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--task-ids", nargs="*", default=None)
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

        seen_benchmarks: dict[int, tuple[Any, ...]] = {}
        seen_proposals: dict[int, tuple[Any, ...]] = {}
        last_status: str | None = None

        while True:
            resp = client.get(f"/jobs/{job_id}", headers=headers)
            resp.raise_for_status()
            job = resp.json()
            status = job["status"]
            best_val_score = job.get("best_val_score")

            if status != last_status:
                score_note = (
                    f"best_val_score={best_val_score:.3f}"
                    if best_val_score is not None
                    else "best_val_score=None"
                )
                print(f"  job status={status} {score_note}")
                last_status = status

            for it in job.get("iterations", []):
                iteration_no = it["iteration_no"]
                bench_key = _benchmark_snapshot(it)
                if (
                    it.get("val_score") is not None
                    and seen_benchmarks.get(iteration_no) != bench_key
                ):
                    _print_iteration_update(it, best_val_score=best_val_score)
                    seen_benchmarks[iteration_no] = bench_key

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

            if status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(args.poll_interval)

        print("\n=== Job summary ===")
        print(json.dumps(
            {
                "id": job["id"],
                "status": job["status"],
                "stop_reason": job.get("stop_reason"),
                "best_val_score": job.get("best_val_score"),
                "best_agent_version_no": job.get("best_agent_version_no"),
                "task_ids": job.get("task_ids"),
            },
            indent=2,
        ))

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
            if it.get("failed_task_ids"):
                print(f"       failed: {', '.join(it['failed_task_ids'])}")
            if it.get("improvement_rationale"):
                print(f"       changes: {it['improvement_rationale'][:160]}")

        if iterations:
            detail = client.get(f"/jobs/{job_id}/iterations/0", headers=headers)
            detail.raise_for_status()
            d = detail.json()
            print(f"\n=== Iteration 0 detail: {len(d.get('task_results', []))} task results ===")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
