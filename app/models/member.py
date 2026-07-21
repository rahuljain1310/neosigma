import enum

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin


class Role(str, enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class Member(IdTimestampMixin, Base):
    """A user inside an organization, authenticated via an API key.

    Only the SHA-256 hash of the key is stored; the plaintext key is shown
    once at creation time.
    """

    __tablename__ = "members"

    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, values_callable=lambda e: [m.value for m in e]), nullable=False
    )
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # First characters of the key, kept for display/debugging.
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
