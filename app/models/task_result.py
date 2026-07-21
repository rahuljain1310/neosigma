import enum

from sqlalchemy import JSON, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin


class TaskStatus(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    # Task produced no verifier result (sandbox/provisioning/timeout issue).
    # Counts as 0.0 in val_score but is flagged so the optimizer LLM is not
    # misled by infrastructure flakes.
    INFRA_ERROR = "infra_error"


class TaskResult(IdTimestampMixin, Base):
    __tablename__ = "task_results"

    iteration_id: Mapped[str] = mapped_column(ForeignKey("iterations.id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(255), nullable=False)

    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, values_callable=lambda e: [m.value for m in e]), nullable=False
    )
    failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full agent conversation trace (messages, tool calls, outputs).
    trace: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    # Raw verifier result.json from the benchmark run.
    verifier_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
