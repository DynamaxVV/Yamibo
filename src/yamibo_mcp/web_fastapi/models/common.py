from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
