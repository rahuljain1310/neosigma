"""RBAC authorization policy.

Rules (org-scoped):
- Every user is restricted to their ``org_id``.
- Admins manage the organization and can see all jobs in that org.
- Members (non-admin) may submit jobs and view only jobs they created.

Role comes from ``User.role`` (DB), not a separate principal type.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from app.models.job import Job
from app.models.user import Role, User


class AuthzDenied(Exception):
    """Policy denial; mapped to HTTP 403 by the API layer."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def can_view_job(user: User, job: Job) -> bool:
    """True when the user may read/cancel the job (and its nested resources)."""
    if job.org_id != user.org_id:
        return False
    if user.role == Role.ADMIN:
        return True
    return job.created_by == user.id


def assert_can_view_job(user: User, job: Job) -> None:
    if not can_view_job(user, job):
        raise AuthzDenied("Not authorized to access this job")


def can_manage_org(user: User, org_id: str) -> bool:
    """True when the user may manage members of ``org_id``."""
    return user.role == Role.ADMIN and user.org_id == org_id


def assert_can_manage_org(user: User, org_id: str) -> None:
    if not can_manage_org(user, org_id):
        raise AuthzDenied("Admin role required for this organization")


def scope_jobs_for_user(stmt: Select[Any], user: User) -> Select[Any]:
    """Restrict a job list query to what the user is allowed to see."""
    stmt = stmt.where(Job.org_id == user.org_id)
    if user.role != Role.ADMIN:
        stmt = stmt.where(Job.created_by == user.id)
    return stmt
