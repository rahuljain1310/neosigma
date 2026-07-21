#!/usr/bin/env python3
"""End-to-end client for the Agent Optimization Service."""

from __future__ import annotations

import argparse
import json
import os
import sys
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

        while True:
            resp = client.get(f"/jobs/{job_id}", headers=headers)
            resp.raise_for_status()
            job = resp.json()
            status = job["status"]
            score = job.get("best_val_score")
            print(f"  status={status} best_val_score={score}")
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

        iterations_resp = client.get(f"/jobs/{job_id}/iterations", headers=headers)
        iterations_resp.raise_for_status()
        iterations = iterations_resp.json()
        print(f"\n=== Iteration history ({len(iterations)} iterations) ===")
        for it in iterations:
            accepted = it.get("accepted")
            mark = "✓" if accepted else ("✗" if accepted is False else "-")
            print(
                f"  [{mark}] iter={it['iteration_no']} "
                f"phase={it['phase']} val_score={it.get('val_score')} "
                f"agent_v={it['agent_version_no']}"
            )
            if it.get("improvement_rationale"):
                print(f"       rationale: {it['improvement_rationale'][:120]}")

        if iterations:
            detail = client.get(f"/jobs/{job_id}/iterations/0", headers=headers)
            detail.raise_for_status()
            d = detail.json()
            print(f"\n=== Iteration 0 detail: {len(d.get('task_results', []))} task results ===")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
