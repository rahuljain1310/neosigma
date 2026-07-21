from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://agentopt:agentopt@localhost:5433/agentopt"

    # Optimizer: "auto" uses the LLM when an API key is present, else the
    # deterministic heuristic optimizer (useful for offline evaluation).
    optimizer_mode: str = "auto"  # auto | llm | heuristic
    optimizer_model: str = "gpt-5.4"

    # Model used by the agent under test (harbor executor).
    agent_model: str = "gpt-5.4"
    openai_api_key: str = ""

    # Harbor executor
    harness_repo_url: str = "https://github.com/neosigmaai/auto-harness.git"
    harness_dir: str = "data/harness"
    harbor_env_provider: str = "docker"  # docker | e2b | daytona | modal
    harbor_n_concurrent: int = 4
    per_task_timeout_sec: int = 1200

    # Worker
    worker_enabled: bool = True
    worker_poll_interval_sec: float = 1.0

    # Loop defaults (overridable per job)
    default_max_iterations: int = 5
    default_patience: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
