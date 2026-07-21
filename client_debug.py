"""Shared helpers for printing and dumping optimizer debug artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

DEBUG_ARTIFACTS_DIR = Path(".debug")


def _section(title: str) -> str:
    return f"\n{'=' * 72}\n{title}\n{'=' * 72}"


def format_optimizer_context(ctx: dict[str, Any] | None) -> str:
    if not ctx:
        return "(no optimizer_context stored — recreate DB or rerun job on latest code)"
    lines = [
        f"source_agent_version_no: {ctx.get('source_agent_version_no')}",
        f"failing_task_count: {ctx.get('failing_task_count')}",
        "",
        "failure_context (what the optimizer saw):",
    ]
    for item in ctx.get("failure_context") or []:
        lines.append(f"\n--- {item.get('task_id')} ({item.get('status')}) ---")
        if item.get("failure_summary"):
            lines.append(f"summary: {item['failure_summary']}")
        trace = item.get("trace_excerpt") or ""
        if trace:
            lines.append(f"trace_excerpt: {trace}")
        else:
            lines.append("trace_excerpt: (empty)")
    return "\n".join(lines)


def format_agent_diff(version: dict[str, Any]) -> str:
    diff = (version.get("diff") or "").strip()
    if not diff:
        return "(no diff — agent.py unchanged from parent)"
    return diff


def print_iteration_debug(
    *,
    iteration_no: int,
    detail: dict[str, Any],
    proposed_version: dict[str, Any] | None,
) -> None:
    print(_section(f"DEBUG iter {iteration_no}"))
    print(
        f"benchmark: agent_v={detail.get('agent_version_no')} "
        f"val_score={detail.get('val_score')} accepted={detail.get('accepted')}"
    )
    print(format_optimizer_context(detail.get("optimizer_context")))

    proposed_no = detail.get("proposed_agent_version_no")
    if proposed_no is None:
        print("\n(no proposal from this iteration)")
        return

    print(f"\nproposal: agent_v={proposed_no}")
    if detail.get("improvement_rationale"):
        print(f"rationale: {detail['improvement_rationale']}")
    if proposed_version:
        print("\nagent.py diff:")
        print(format_agent_diff(proposed_version))
    if detail.get("llm_response"):
        print("\noptimizer raw response:")
        print(detail["llm_response"][:2000])


def dump_iteration_debug(
    *,
    dump_dir: Path,
    iteration_no: int,
    detail: dict[str, Any],
    proposed_version: dict[str, Any] | None,
) -> None:
    out = dump_dir / f"iter_{iteration_no:02d}"
    out.mkdir(parents=True, exist_ok=True)

    (out / "iteration.json").write_text(json.dumps(detail, indent=2, default=str))
    if detail.get("llm_prompt"):
        (out / "optimizer_prompt.txt").write_text(detail["llm_prompt"])
    if detail.get("optimizer_context"):
        (out / "optimizer_context.json").write_text(
            json.dumps(detail["optimizer_context"], indent=2, default=str)
        )

    traces_dir = out / "benchmark_traces"
    traces_dir.mkdir(exist_ok=True)
    for tr in detail.get("task_results") or []:
        task_id = tr["task_id"]
        (traces_dir / f"{task_id}.json").write_text(
            json.dumps(tr, indent=2, default=str)
        )

    proposed_no = detail.get("proposed_agent_version_no")
    if proposed_version:
        (out / f"agent_v{proposed_no}.py").write_text(proposed_version.get("content", ""))
        diff = proposed_version.get("diff")
        if diff:
            (out / f"agent_v{proposed_no}.diff").write_text(diff)


def fetch_iteration_debug_bundle(
    client: httpx.Client,
    job_id: str,
    iteration_no: int,
    headers: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    detail_resp = client.get(
        f"/jobs/{job_id}/iterations/{iteration_no}",
        headers=headers,
    )
    detail_resp.raise_for_status()
    detail = detail_resp.json()

    proposed_version = None
    proposed_no = detail.get("proposed_agent_version_no")
    if proposed_no is not None:
        version_resp = client.get(
            f"/jobs/{job_id}/agent-versions/{proposed_no}",
            headers=headers,
        )
        if version_resp.status_code == 200:
            proposed_version = version_resp.json()
    return detail, proposed_version


def debug_completed_job(
    client: httpx.Client,
    job_id: str,
    headers: dict[str, str],
    *,
    dump_dir: Path | None = None,
    print_stdout: bool = True,
) -> None:
    job_resp = client.get(f"/jobs/{job_id}", headers=headers)
    job_resp.raise_for_status()
    job = job_resp.json()
    iterations = job.get("iterations") or []

    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)
        (dump_dir / "job_summary.json").write_text(json.dumps(job, indent=2, default=str))

    for it in iterations:
        iteration_no = it["iteration_no"]
        detail, proposed_version = fetch_iteration_debug_bundle(
            client, job_id, iteration_no, headers
        )
        if print_stdout:
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

    if dump_dir:
        print(f"\nDebug artifacts written to {dump_dir.resolve()}")
