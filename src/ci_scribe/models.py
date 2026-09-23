from __future__ import annotations

from pydantic import BaseModel, Field


class EvalTriage(BaseModel):
    """Schema eval-triage-v1: diagnóstico de una corrida de eval de la flota."""

    summary: str
    probable_cause: list[str] = Field(default_factory=list)
    severity: str
    verdict: str
    actions: list[str] = Field(default_factory=list)
    requires_rebaseline: bool = False