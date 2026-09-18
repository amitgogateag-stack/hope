from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ARRAY, BigInteger, Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.domain.strategy.candidates import (
    StrategyCandidateClassification,
    StrategyCandidateState,
    StrategyMarket,
)


class StrategyCandidateClassificationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classification_id: UUID
    classification_sequence: int
    strategy_version_id: UUID
    markets: tuple[StrategyMarket, ...]
    state: StrategyCandidateState
    research_decision_id: str | None
    rationale: str
    created_at: datetime


class CurrentStrategyCandidateRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classification_id: UUID
    classification_sequence: int
    strategy_version_id: UUID
    family: str
    markets: tuple[StrategyMarket, ...]
    state: StrategyCandidateState
    research_decision_id: str | None
    rationale: str
    created_at: datetime


class SqlAlchemyStrategyCandidateRepository:
    """Append-only, idempotent strategy candidate classification history."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._classifications = Table(
            "strategy_candidate_classifications",
            metadata,
            Column("classification_id", Uuid, primary_key=True),
            Column("classification_sequence", BigInteger, nullable=False),
            Column("strategy_version_id", Uuid, nullable=False),
            Column("markets", ARRAY(String), nullable=False),
            Column("state", String, nullable=False),
            Column("research_decision_id", String, nullable=True),
            Column("rationale", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    @staticmethod
    def deterministic_id(definition: StrategyCandidateClassification) -> UUID:
        identity = ":".join(
            (
                "hope:strategy-candidate-classification",
                str(definition.strategy_version_id),
                ",".join(sorted(m.value for m in definition.markets)),
                definition.state.value,
                definition.research_decision_id or "",
                definition.rationale,
            )
        )
        return uuid5(NAMESPACE_URL, identity)

    def append(self, definition: StrategyCandidateClassification) -> bool:
        classification_id = self.deterministic_id(definition)
        statement = (
            pg_insert(self._classifications)
            .values(
                classification_id=classification_id,
                strategy_version_id=definition.strategy_version_id,
                markets=sorted(m.value for m in definition.markets),
                state=definition.state.value,
                research_decision_id=definition.research_decision_id,
                rationale=definition.rationale,
            )
            .on_conflict_do_nothing(index_elements=["classification_id"])
            .returning(self._classifications.c.classification_id)
        )
        return self._connection.execute(statement).scalar_one_or_none() is not None

    def history(
        self, strategy_version_id: UUID
    ) -> list[StrategyCandidateClassificationRecord]:
        rows = self._connection.execute(
            select(self._classifications)
            .where(
                self._classifications.c.strategy_version_id == strategy_version_id
            )
            .order_by(
                self._classifications.c.created_at,
                self._classifications.c.classification_id,
            )
        ).mappings().all()
        return [StrategyCandidateClassificationRecord(**row) for row in rows]

    def current(self) -> list[CurrentStrategyCandidateRecord]:
        metadata = MetaData()
        current = Table(
            "current_strategy_candidate_classifications",
            metadata,
            Column("classification_id", Uuid),
            Column("classification_sequence", BigInteger),
            Column("strategy_version_id", Uuid),
            Column("family", String),
            Column("markets", ARRAY(String)),
            Column("state", String),
            Column("research_decision_id", String),
            Column("rationale", String),
            Column("created_at", DateTime(timezone=True)),
        )
        rows = self._connection.execute(
            select(current).order_by(current.c.classification_sequence)
        ).mappings().all()
        return [CurrentStrategyCandidateRecord(**row) for row in rows]
