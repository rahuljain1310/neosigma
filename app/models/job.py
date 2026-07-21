import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(str, enum.Enum):
    MAX_ITERATIONS = "max_iterations"
    NO_IMPROVEMENT = "no_improvement"
    ALL_TASKS_PASSED = "all_tasks_passed"
    CANCELLED = "cancelled"
    ERROR = "error"


class Job(IdTimestampMixin, Base):
    """One benchmark-optimization run: baseline + N optimization iterations."""

    __tablename__ = "jobs"

    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, values_callable=lambda e: [m.value for m in e]),
        default=JobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    stop_reason: Mapped[StopReason | None] = mapped_column(
        Enum(StopReason, values_callable=lambda e: [m.value for m in e]), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Benchmark subset + loop configuration.
    task_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    patience: Mapped[int] = mapped_column(Integer, nullable=False)
    executor: Mapped[str] = mapped_column(String(32), nullable=False)  # simulated | harbor
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Accumulated learnings log — the service's memory across iterations,
    # fed into every optimizer prompt (DB-resident analogue of learnings.md).
    learnings: Mapped[str] = mapped_column(Text, default="", nullable=False)

    best_val_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_agent_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
