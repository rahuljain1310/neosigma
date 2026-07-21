import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import DEFAULT_USER_PASSWORD, hash_password
from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.organization import Organization
from app.models.user import Role, User
from app.worker.processor import JobProcessor


@pytest.fixture
async def test_env(monkeypatch):
    monkeypatch.setenv("OPTIMIZER_MODE", "heuristic")
    from app.config import get_settings

    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        org = Organization(name="test-org")
        session.add(org)
        await session.flush()
        user = User(
            org_id=org.id,
            email="tester@example.com",
            name="tester",
            role=Role.ADMIN,
            password_hash=hash_password(DEFAULT_USER_PASSWORD),
        )
        session.add(user)
        await session.commit()

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, "test-org", "tester@example.com"

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    await engine.dispose()


async def _auth_headers(client: AsyncClient, org_name: str, email: str) -> dict[str, str]:
    resp = await client.post(
        "/auth/login",
        json={"org_name": org_name, "email": email, "password": DEFAULT_USER_PASSWORD},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health(test_env):
    client, _, _, _ = test_env
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_login_refresh_me_and_logout(test_env):
    client, _, org_name, email = test_env
    login = await client.post(
        "/auth/login",
        json={"org_name": org_name, "email": email, "password": DEFAULT_USER_PASSWORD},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    profile = me.json()
    assert profile["email"] == email
    assert profile["org_name"] == org_name
    assert profile["role"] == "admin"

    refreshed = await client.post(
        "/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    new_refresh = refreshed.json()["refresh_token"]

    # Old refresh token was rotated away.
    reused = await client.post(
        "/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert reused.status_code == 401
    assert "Invalid or expired refresh token" in reused.json()["detail"]

    logout = await client.post("/auth/logout", json={"refresh_token": new_refresh})
    assert logout.status_code == 200
    assert logout.json()["revoked"] is True

    after_logout = await client.post(
        "/auth/refresh",
        json={"refresh_token": new_refresh},
    )
    assert after_logout.status_code == 401


@pytest.mark.asyncio
async def test_org_user_bootstrap_then_requires_admin(test_env):
    client, _, _, _ = test_env

    created_org = await client.post("/orgs", json={"name": "acme-bootstrap"})
    assert created_org.status_code == 201
    org_id = created_org.json()["id"]

    first = await client.post(
        f"/orgs/{org_id}/users",
        json={
            "email": "owner@acme.example.com",
            "password": DEFAULT_USER_PASSWORD,
            "name": "owner",
            "role": "member",
        },
    )
    assert first.status_code == 201
    assert first.json()["role"] == "admin"

    # Stale bearer must not block first-user bootstrap on a fresh org.
    stale_org = await client.post("/orgs", json={"name": "acme-stale-token"})
    assert stale_org.status_code == 201
    stale_org_id = stale_org.json()["id"]
    bootstrapped = await client.post(
        f"/orgs/{stale_org_id}/users",
        json={
            "email": "owner@stale.example.com",
            "password": DEFAULT_USER_PASSWORD,
            "name": "owner",
            "role": "member",
        },
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert bootstrapped.status_code == 201
    assert bootstrapped.json()["role"] == "admin"

    second = await client.post(
        f"/orgs/{org_id}/users",
        json={
            "email": "member@acme.example.com",
            "password": DEFAULT_USER_PASSWORD,
            "name": "member",
            "role": "member",
        },
    )
    assert second.status_code == 401
    assert "Authorization" in second.json()["detail"]

    headers = await _auth_headers(client, "acme-bootstrap", "owner@acme.example.com")
    member = await client.post(
        f"/orgs/{org_id}/users",
        json={
            "email": "member@acme.example.com",
            "password": DEFAULT_USER_PASSWORD,
            "name": "member",
            "role": "member",
        },
        headers=headers,
    )
    assert member.status_code == 201
    assert member.json()["role"] == "member"


@pytest.mark.asyncio
async def test_optimization_loop(test_env):
    client, factory, org_name, email = test_env
    headers = await _auth_headers(client, org_name, email)

    created = await client.post(
        "/jobs",
        json={
            "task_ids": ["regex-log", "fix-git", "cobol-modernization"],
            "max_iterations": 2,
            "patience": 2,
            "executor": "simulated",
        },
        headers=headers,
    )
    assert created.status_code == 202
    job_id = created.json()["id"]

    listed = await client.get("/jobs", headers=headers)
    assert listed.status_code == 200
    assert any(job["id"] == job_id for job in listed.json())

    async with factory() as session:
        await JobProcessor(session).process(job_id)

    detail = await client.get(f"/jobs/{job_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["best_val_score"] is not None
    assert len(body["iterations"]) >= 2
    assert body["iterations"][0]["tasks_passed"] + body["iterations"][0]["tasks_failed"] > 0
    assert body["iterations"][0]["tasks_completed"] == len(body["task_ids"])
    assert body["iterations"][0]["tasks_pending"] == 0
    assert body["iterations"][0]["tasks_running"] == 0
    assert body["latest_task_results"]
    assert {t["status"] for t in body["latest_task_results"]} <= {"passed", "failed", "infra_error"}
    for task in body["latest_task_results"]:
        if task["status"] == "failed":
            assert task["failure_summary"]

    queued_only = await client.get("/jobs", params={"status": "queued"}, headers=headers)
    assert queued_only.status_code == 200
    assert all(job["status"] == "queued" for job in queued_only.json())


@pytest.mark.asyncio
async def test_cancel_job_marks_it_terminal_and_processor_does_not_revive_it(test_env):
    client, factory, org_name, email = test_env
    headers = await _auth_headers(client, org_name, email)
    created = await client.post(
        "/jobs",
        json={
            "task_ids": ["regex-log"],
            "max_iterations": 1,
            "patience": 1,
            "executor": "simulated",
        },
        headers=headers,
    )
    job_id = created.json()["id"]

    cancelled = await client.post(f"/jobs/{job_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["stop_reason"] == "cancelled"
    assert cancelled.json()["finished_at"] is not None

    async with factory() as session:
        await JobProcessor(session).process(job_id)

    detail = await client.get(f"/jobs/{job_id}", headers=headers)
    assert detail.json()["status"] == "cancelled"
    assert detail.json()["iterations"] == []
