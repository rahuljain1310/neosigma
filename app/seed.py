"""Bootstrap a default org + admin API key on first startup."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import generate_api_key, hash_api_key
from app.models.member import Member, Role
from app.models.organization import Organization

DEFAULT_ORG = "default"
DEFAULT_ADMIN = "admin"


async def ensure_seed_data(session: AsyncSession) -> str | None:
    """Create default org/admin if the database is empty.

    Returns the plaintext admin API key when a new admin is created (first run only).
    """
    result = await session.execute(select(Organization).limit(1))
    if result.scalar_one_or_none() is not None:
        return None

    org = Organization(name=DEFAULT_ORG)
    session.add(org)
    await session.flush()

    api_key = generate_api_key()
    member = Member(
        org_id=org.id,
        name=DEFAULT_ADMIN,
        role=Role.ADMIN,
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key[:12],
    )
    session.add(member)
    await session.commit()
    return api_key
