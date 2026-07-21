import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import generate_api_key, hash_api_key
from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.member import Member, Role
from app.models.organization import Organization
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
        api_key = generate_api_key()
        member = Member(
            org_id=org.id,
            name="tester",
            role=Role.ADMIN,
            api_key_hash=hash_api_key(api_key),
            api_key_prefix=api_key[:12],
        )
        session.add(member)
        await session.commit()

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, api_key

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_health(test_env):
    client, _, _ = test_env
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_optimization_loop(test_env):
    client, factory, api_key = test_env
    headers = {"Authorization": f"Bearer {api_key}"}

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

    iters = await client.get(f"/jobs/{job_id}/iterations", headers=headers)
    assert iters.status_code == 200
    assert len(iters.json()) >= 2

    iter0 = await client.get(f"/jobs/{job_id}/iterations/0", headers=headers)
    assert iter0.status_code == 200
    assert len(iter0.json()["task_results"]) == 3
