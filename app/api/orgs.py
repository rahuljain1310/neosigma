from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.db import get_session
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import UserCreate, UserResponse
from app.schemas.common import ErrorResponse
from app.schemas.organization import OrganizationCreate, OrganizationResponse

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}},
    summary="Create an organization",
)
async def create_org(
    body: OrganizationCreate,
    session: AsyncSession = Depends(get_session),
) -> Organization:
    existing = await session.execute(select(Organization).where(Organization.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Organization already exists")
    org = Organization(name=body.name)
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@router.post(
    "/{org_id}/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Create a user in an organization",
)
async def create_user(
    org_id: str,
    body: UserCreate,
    session: AsyncSession = Depends(get_session),
) -> User:
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    existing = await session.execute(
        select(User).where(User.org_id == org_id, User.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User already exists in this organization")

    user = User(
        org_id=org_id,
        email=body.email,
        name=body.name,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
