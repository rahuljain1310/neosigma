from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.harness.template import AGENT_TEMPLATE


@dataclass
class HarnessWorkspace:
    """A disposable on-disk checkout of auto-harness for one job."""

    root: Path
    job_id: str

    @property
    def agent_path(self) -> Path:
        return self.root / "agent" / "agent.py"

    @property
    def traces_dir(self) -> Path:
        return self.root / "workspace" / "traces" / "latest"

    def write_agent(self, content: str) -> None:
        self.agent_path.parent.mkdir(parents=True, exist_ok=True)
        self.agent_path.write_text(content)

    def ensure_repo(self) -> None:
        settings = get_settings()
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / "benchmark.py").exists():
            subprocess.run(
                ["git", "clone", "--depth", "1", settings.harness_repo_url, str(self.root)],
                check=True,
                capture_output=True,
                text=True,
            )
        # Always start from a clean agent template if missing.
        if not self.agent_path.exists():
            self.write_agent(AGENT_TEMPLATE)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def unified_diff(old: str, new: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="agent.py (old)",
            tofile="agent.py (new)",
        )
    )


def make_workspace(job_id: str) -> HarnessWorkspace:
    settings = get_settings()
    root = Path(settings.harness_dir) / job_id
    return HarnessWorkspace(root=root, job_id=job_id)
