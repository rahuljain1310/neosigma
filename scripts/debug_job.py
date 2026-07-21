#!/usr/bin/env python3
"""Dump optimizer traces and agent.py edits for a completed (or in-progress) job."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx

from app.auth import DEFAULT_ORG_NAME, DEFAULT_USER_EMAIL, DEFAULT_USER_PASSWORD
from client_debug import DEBUG_ARTIFACTS_DIR, debug_completed_job
from test_client import login

DEFAULT_BASE = os.environ.get("AOS_BASE_URL", "http://localhost:8000")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect optimizer inputs (traces) and agent.py edits for a job",
    )
    parser.add_argument("job_id", help="Job ID to inspect")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--org", default=os.environ.get("AOS_ORG", DEFAULT_ORG_NAME))
    parser.add_argument("--email", default=os.environ.get("AOS_EMAIL", DEFAULT_USER_EMAIL))
    parser.add_argument(
        "--password",
        default=os.environ.get("AOS_PASSWORD", DEFAULT_USER_PASSWORD),
    )
    parser.add_argument(
        "--dump-dir",
        type=Path,
        default=None,
        help="Write per-iteration traces, prompts, and diffs to this directory",
    )
    parser.add_argument(
        "--no-print",
        action="store_true",
        help="Only write --dump-dir files, do not print to stdout",
    )
    args = parser.parse_args()

    dump_dir = args.dump_dir
    if dump_dir is None:
        dump_dir = DEBUG_ARTIFACTS_DIR / args.job_id

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        token = login(client, args.org, args.email, args.password)
        headers = {"Authorization": f"Bearer {token}"}
        debug_completed_job(
            client,
            args.job_id,
            headers,
            dump_dir=dump_dir,
            print_stdout=not args.no_print,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
