from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.api.jobs import router as jobs_router
from app.api.orgs import router as orgs_router
from app.config import get_settings
from app.db import dispose_engine, init_db, session_factory
from app.schemas.common import HealthResponse
from app.seed import ensure_seed_data
from app.worker import Worker

logger = logging.getLogger(__name__)
_worker: Worker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker
    logging.basicConfig(level=logging.INFO)
    await init_db()

    async with session_factory()() as session:
        api_key = await ensure_seed_data(session)
        if api_key:
            logger.warning(
                "Created default admin API key (store it now): %s",
                api_key,
            )

    settings = get_settings()
    if settings.worker_enabled:
        _worker = Worker()
        await _worker.start()

    yield

    if _worker:
        await _worker.stop()
    await dispose_engine()


app = FastAPI(
    title="Agent Optimization Service",
    description=(
        "Runs an agent against a TerminalBench subset in sandboxes, mines failures, "
        "and iteratively improves agent/agent.py via an LLM optimization loop."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(jobs_router)
app.include_router(orgs_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")
