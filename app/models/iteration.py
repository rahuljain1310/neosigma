import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin


class IterationPhase(str, enum.Enum):
    PENDING = "pending"
    RUNNING_BENCHMARK = "running_benchmark"
    ANALYZING = "analyzing"
    PROPOSING = "proposing"
    DONE = "done"
    FAILED = "failed"


class Iteration(IdTimestampMixin, Base):
    """One cycle of the loop: run benchmark with a version, analyze, propose next.

    Iteration 0 is the baseline run of the template agent (no proposal is
    judged against it; it just sets the starting score).
    """

    __tablename__ = "iterations"
    __table_args__ = (UniqueConstraint("job_id", "iteration_no"),)

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    iteration_no: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_version_no: Mapped[int] = mapped_column(Integer, nullable=False)

    phase: Mapped[IterationPhase] = mapped_column(
        Enum(IterationPhase, values_callable=lambda e: [m.value for m in e]),
        default=IterationPhase.PENDING,
        nullable=False,
    )

    val_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # None until judged; baseline iteration is always accepted.
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Per-phase timestamps for observability of the async run.
    bench_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bench_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optimizer artifacts (what was proposed *after* this iteration's run).
    llm_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvement_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    learnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured snapshot of failing tasks/traces fed to the optimizer.
    optimizer_context: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)

    executor_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
