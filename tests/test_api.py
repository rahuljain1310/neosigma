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
async def test_login_and_refresh(test_env):
    client, _, org_name, email = test_env
    login = await client.post(
        "/auth/login",
        json={"org_name": org_name, "email": email, "password": DEFAULT_USER_PASSWORD},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    refreshed = await client.post(
        "/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


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
