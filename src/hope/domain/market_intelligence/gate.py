from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IntelligenceEntryGateContext(BaseModel):
    """Pre-resolved intelligence blockers for one entry decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocker_assessment_ids: tuple[UUID, ...] = ()

    @property
    def blocks_entry(self) -> bool:
        return bool(self.blocker_assessment_ids)
