from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import generate_api_key, hash_api_key, require_admin
from app.db import get_session
from app.models.member import Member
from app.models.organization import Organization
from app.schemas.auth import ApiKeyCreated, MemberCreate, MemberResponse
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
    _admin: Member = Depends(require_admin),
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
    "/{org_id}/members",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
    summary="Create a member and issue an API key",
)
async def create_member(
    org_id: str,
    body: MemberCreate,
    session: AsyncSession = Depends(get_session),
    _admin: Member = Depends(require_admin),
) -> ApiKeyCreated:
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    api_key = generate_api_key()
    member = Member(
        org_id=org_id,
        name=body.name,
        role=body.role,
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key[:12],
    )
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return ApiKeyCreated(**MemberResponse.model_validate(member).model_dump(), api_key=api_key)
