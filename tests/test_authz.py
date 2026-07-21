"""Unit tests for RBAC policy (no HTTP)."""

import pytest
from fastapi import HTTPException

from app.authz import (
    AuthzDenied,
    admin,
    assert_can_manage_org,
    assert_can_view_job,
    can_manage_org,
    can_view_job,
)
from app.models.job import Job, JobStatus
from app.models.user import Role, User


def _user(*, user_id: str, org_id: str, admin: bool) -> User:
    return User(
        id=user_id,
        org_id=org_id,
        email=f"{user_id}@example.com",
        name=user_id,
        role=Role.ADMIN if admin else Role.MEMBER,
        password_hash="x",
    )


def _job(*, org_id: str, created_by: str) -> Job:
    return Job(
        id="job1",
        org_id=org_id,
        created_by=created_by,
        status=JobStatus.QUEUED,
        task_ids=["regex-log"],
        max_iterations=1,
        patience=1,
        executor="simulated",
        config={},
    )


def test_admin_can_view_any_job_in_org():
    admin_user = _user(user_id="admin", org_id="org-a", admin=True)
    job = _job(org_id="org-a", created_by="member-1")
    assert can_view_job(admin_user, job) is True
    assert_can_view_job(admin_user, job)


def test_member_can_view_own_job_only():
    member = _user(user_id="member-1", org_id="org-a", admin=False)
    own = _job(org_id="org-a", created_by="member-1")
    other = _job(org_id="org-a", created_by="member-2")
    assert can_view_job(member, own) is True
    assert can_view_job(member, other) is False
    with pytest.raises(AuthzDenied):
        assert_can_view_job(member, other)


def test_cross_org_job_is_forbidden():
    member = _user(user_id="member-1", org_id="org-a", admin=False)
    admin_user = _user(user_id="admin", org_id="org-a", admin=True)
    foreign = _job(org_id="org-b", created_by="someone")
    assert can_view_job(member, foreign) is False
    assert can_view_job(admin_user, foreign) is False
    with pytest.raises(AuthzDenied) as exc:
        assert_can_view_job(admin_user, foreign)
    assert exc.value.detail == "Not authorized to access this job"


def test_only_org_admin_can_manage_org():
    admin_user = _user(user_id="admin", org_id="org-a", admin=True)
    member = _user(user_id="member", org_id="org-a", admin=False)
    other_admin = _user(user_id="admin-b", org_id="org-b", admin=True)

    assert can_manage_org(admin_user, "org-a") is True
    assert can_manage_org(member, "org-a") is False
    assert can_manage_org(other_admin, "org-a") is False

    assert_can_manage_org(admin_user, "org-a")
    with pytest.raises(AuthzDenied):
        assert_can_manage_org(member, "org-a")
    with pytest.raises(AuthzDenied):
        assert_can_manage_org(other_admin, "org-a")


@pytest.mark.asyncio
async def test_admin_decorator_allows_admin_and_rejects_member():
    @admin
    async def only_admins(*, user: User) -> str:
        return f"ok:{user.id}"

    assert getattr(only_admins, "__authz_requires_admin__") is True

    admin_user = _user(user_id="admin", org_id="org-a", admin=True)
    assert await only_admins(user=admin_user) == "ok:admin"

    member = _user(user_id="member", org_id="org-a", admin=False)
    with pytest.raises(HTTPException) as forbidden:
        await only_admins(user=member)
    assert forbidden.value.status_code == 403
    assert forbidden.value.detail == "Admin role required"

    with pytest.raises(HTTPException) as unauthorized:
        await only_admins(user=None)  # type: ignore[arg-type]
    assert unauthorized.value.status_code == 401
