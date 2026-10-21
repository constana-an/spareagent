from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

# W1 accepts only public github.com repositories, as https://github.com/<owner>/<repo>[.git]
_REPO_URL = re.compile(
    r"^https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$"
)
_SHA = re.compile(r"^[a-fA-F0-9]{40}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")


def validate_repository_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        not _REPO_URL.fullmatch(value)
        or parts.username
        or parts.password
        or parts.port
        or parts.query
        or parts.fragment
        or ".." in parts.path
    ):
        raise ValueError("must be a public https://github.com/<owner>/<repo> URL")
    return value


def validate_commit_sha(value: str) -> str:
    if not _SHA.fullmatch(value):
        raise ValueError("must be a full 40-character hexadecimal commit SHA")
    return value.lower()


def validate_prompt(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal["CODE_FIX", "REFACTOR", "WRITE_TESTS"]
    repository_url: StrictStr = Field(max_length=200)
    commit_sha: StrictStr
    prompt: Annotated[StrictStr, Field(min_length=1, max_length=12000)]
    max_budget_cents: Annotated[StrictInt, Field(ge=1, le=10_000_000)]
    max_duration_seconds: Annotated[StrictInt, Field(ge=10, le=7200)]

    @field_validator("repository_url")
    @classmethod
    def _repo(cls, value: str) -> str:
        return validate_repository_url(value)

    @field_validator("commit_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        return validate_commit_sha(value)

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        return validate_prompt(value)


class TaskOut(BaseModel):
    id: uuid.UUID
    status: str
    task_type: str
    max_budget_cents: int
    created_at: datetime
    result_summary: str | None = None


class TaskList(BaseModel):
    items: list[TaskOut]
    next_cursor: str | None


class ArtifactOut(BaseModel):
    id: uuid.UUID
    kind: Literal[
        "PATCH",
        "TEST_REPORT",
        "LOG",
        "SUMMARY",
        "EXECUTION_LOG",
        "VERIFICATION_REPORT",
        "DELIVERABLE",
    ]
    sha256: str
    media_type: str
    source: Literal["WORKER", "PLATFORM"]
    name: str | None = None  # 0012: deliverable file name


class ArtifactList(BaseModel):
    items: list[ArtifactOut]
