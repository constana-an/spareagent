from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


class ChatMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "developer", "user", "assistant"]
    content: Annotated[StrictStr, Field(max_length=100_000)]


class ChatRequest(BaseModel):
    # Unsupported OpenAI parameters are rejected explicitly instead of silently ignored.
    model_config = ConfigDict(extra="forbid")

    model: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    stream: StrictBool = False
    max_tokens: Annotated[StrictInt, Field(ge=1, le=8192)] | None = None
    temperature: Annotated[StrictFloat, Field(ge=0, le=2)] | None = None
    messages: Annotated[list[ChatMessageIn], Field(min_length=1, max_length=256)]


class ModelOut(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelOut]
