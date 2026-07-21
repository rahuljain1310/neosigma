from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    generate_refresh_token,
    get_current_user,
    hash_token,
    verify_password,
)
from app.config import get_settings
from app.db import get_session
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Log in to an organization",
)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    org_result = await session.execute(
        select(Organization).where(Organization.name == body.org_name)
    )
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid organization, email, or password")

    user_result = await session.execute(
        select(User).where(User.org_id == org.id, User.email == body.email)
    )
    user = user_result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid organization, email, or password")

    return await _issue_tokens(session, user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Refresh access token",
)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    token_hash = hash_token(body.refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored is None or not stored.is_active():
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await session.get(User, stored.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    stored.revoked_at = datetime.now(timezone.utc)
    return await _issue_tokens(session, user)


@router.get(
    "/me",
    responses={401: {"model": ErrorResponse}},
    summary="Get the current authenticated user",
)
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": user.id,
        "org_id": user.org_id,
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
    }


async def _issue_tokens(session: AsyncSession, user: User) -> TokenResponse:
    settings = get_settings()
    access_token = create_access_token(user)

    refresh_plain = generate_refresh_token()
    refresh = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_plain),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(refresh)
    await session.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_plain,
        expires_in=settings.access_token_expire_minutes * 60,
    )
