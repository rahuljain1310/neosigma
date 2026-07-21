from pydantic import BaseModel, Field


class AgentVersionSummary(BaseModel):
    version_no: int
    parent_version_no: int | None
    sha256: str
    created_by_iteration: int | None = Field(
        default=None,
        description="Iteration whose optimizer produced this version (None for v0).",
    )
    diff: str | None = Field(
        default=None,
        description="Unified diff against parent version.",
    )

    model_config = {"from_attributes": True}


class AgentVersionDetail(AgentVersionSummary):
    content: str = Field(description="Full agent/agent.py source for this version.")
