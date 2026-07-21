from datetime import datetime

from pydantic import BaseModel, Field

from app.models.member import Role


class MemberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["alice"])
    role: Role = Role.MEMBER


class MemberResponse(BaseModel):
    id: str
    org_id: str
    name: str
    role: Role
    api_key_prefix: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(MemberResponse):
    api_key: str = Field(
        ...,
        description="Plaintext API key — shown once at creation; store it securely.",
    )
