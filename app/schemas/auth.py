from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.user import Role


class UserCreate(BaseModel):
    email: EmailStr = Field(..., examples=["alice@example.com"])
    password: str = Field(..., min_length=8, examples=["assignment-password"])
    name: str = Field(..., min_length=1, max_length=255, examples=["alice"])
    role: Role = Role.MEMBER


class UserResponse(BaseModel):
    id: str = Field(description="User ID (32-character lowercase hex).")
    org_id: str = Field(description="Organization ID (32-character lowercase hex).")
    email: str
    name: str
    role: Role = Field(description="admin manages the org; member submits/views own jobs.")
    created_at: datetime

    model_config = {"from_attributes": True}


class MeResponse(UserResponse):
    """Current authenticated user plus organization name."""

    org_name: str = Field(description="Name of the user's organization.")


class LoginRequest(BaseModel):
    org_name: str = Field(..., examples=["default"])
    email: EmailStr = Field(..., examples=["admin@example.com"])
    password: str = Field(..., examples=["assignment-password"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(description="Refresh token previously issued by login or refresh.")


class LogoutRequest(BaseModel):
    refresh_token: str = Field(description="Refresh token to revoke.")


class LogoutResponse(BaseModel):
    revoked: bool = Field(description="True if a matching active refresh token was revoked.")
