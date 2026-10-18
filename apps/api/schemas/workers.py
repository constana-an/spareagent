from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkerRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    capabilities: dict[str, Any]


class ResourceApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    license_reference: str | None = Field(default=None, max_length=500)


class ResourceOut(BaseModel):
    id: uuid.UUID
    resource_type: str
    authorization_status: str
    public_eligible: bool
