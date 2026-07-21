from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.user import Role


class UserCreate(BaseModel):
    email: EmailStr = Field(..., examples=["alice@example.com"])
    password: str = Field(..., min_length=8, examples=["assignment-password"])
    name: str = Field(..., min_length=1, max_length=255, examples=["alice"])
    role: Role = Role.MEMBER


class UserResponse(BaseModel):
    id: str
    org_id: str
    email: str
    name: str
    role: Role
    created_at: datetime

    model_config = {"from_attributes": True}


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
    refresh_token: str
