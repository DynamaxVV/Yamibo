from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobActionRequest(BaseModel):
    job_id: str = Field(..., min_length=1)


class JobControlRequest(BaseModel):
    action: Literal["pause", "resume"]


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
