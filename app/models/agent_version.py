from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin


class AgentVersion(IdTimestampMixin, Base):
    """A full snapshot of agent/agent.py.

    Version 0 is the starting template; each accepted-or-rejected proposal
    becomes a new version. This replaces auto-harness's use of git commits
    as the version store.
    """

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("job_id", "version_no"),)

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Iteration number whose optimizer produced this version (None for v0).
    created_by_iteration: Mapped[int | None] = mapped_column(Integer, nullable=True)
