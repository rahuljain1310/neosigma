"""Bootstrap default org + admin user on first startup."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    DEFAULT_ORG_NAME,
    DEFAULT_USER_EMAIL,
    DEFAULT_USER_PASSWORD,
    hash_password,
)
from app.models.organization import Organization
from app.models.user import Role, User


async def ensure_seed_data(session: AsyncSession) -> None:
    result = await session.execute(select(Organization).limit(1))
    if result.scalar_one_or_none() is not None:
        return

    org = Organization(name=DEFAULT_ORG_NAME)
    session.add(org)
    await session.flush()

    user = User(
        org_id=org.id,
        email=DEFAULT_USER_EMAIL,
        name="admin",
        role=Role.ADMIN,
        password_hash=hash_password(DEFAULT_USER_PASSWORD),
    )
    session.add(user)
    await session.commit()
