"""Onboarding wizard schemas (#224, Onboarding R2)."""

from typing import Literal

from pydantic import BaseModel, Field


class StaffMember(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    # a writer (ops) can mint ops/viewer; minting an admin requires an
    # admin caller — enforced in the route
    role: Literal["ops", "viewer", "admin"] = "ops"
    password: str | None = Field(None, min_length=6)  # generated when absent


class StaffIssueRequest(BaseModel):
    users: list[StaffMember] = Field(..., min_length=1)


class StaffResult(BaseModel):
    username: str
    role: str
    status: Literal["created", "exists"]
    # echoed ONCE for created users — the wizard is the only time the
    # generated password is visible
    password: str | None = None


class ReadinessSection(BaseModel):
    key: str
    label: str
    ok: bool
    detail: str


class ReadinessResponse(BaseModel):
    ready: bool
    sections: list[ReadinessSection]
