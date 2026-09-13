from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, Numeric, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.risk import paper_risk_payload_hash
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperRiskRepository:
    """Persist PAPER risk assessments with durable idempotency evidence."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._assessments = Table(
            "paper_risk_assessments",
            metadata,
            Column("signal_id", Uuid, primary_key=True),
            Column("decision", String, nullable=False),
            Column("reason_code", String, nullable=False),
            Column("approved_quantity", Numeric, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _get_row(self, signal_id: UUID):
        return self._connection.execute(
            select(
                self._assessments.c.signal_id,
                self._assessments.c.decision,
                self._assessments.c.reason_code,
                self._assessments.c.approved_quantity,
            ).where(self._assessments.c.signal_id == signal_id)
        ).mappings().one_or_none()

    @staticmethod
    def _assert_row_matches(row, assessment: RiskAssessment) -> None:
        if (
            row["signal_id"] != assessment.signal_id
            or row["decision"] != assessment.decision.value
            or row["reason_code"] != assessment.reason_code
            or row["approved_quantity"] != assessment.approved_quantity
        ):
            raise ValueError("PAPER_RISK_DURABLE_STATE_CONFLICT")

    def persist(self, effect: PaperEffect, assessment: RiskAssessment) -> bool:
        if effect.effect_type is not PaperEffectType.RISK or effect.entity_id != assessment.signal_id:
            raise ValueError("PAPER_RISK_EFFECT_MISMATCH")
        if effect.payload_hash != paper_risk_payload_hash(assessment):
            raise ValueError("PAPER_RISK_EFFECT_PAYLOAD_MISMATCH")
        if assessment.decision is RiskDecision.REJECT and assessment.approved_quantity != 0:
            raise ValueError("PAPER_RISK_REJECTION_QUANTITY_INVALID")
        if self._effects.get(PaperEffectType.SIGNAL, assessment.signal_id) is None:
            raise ValueError("PAPER_RISK_SOURCE_SIGNAL_UNTRACKED")

        with self._connection.begin_nested():
            existing_effect = self._effects.get(PaperEffectType.RISK, assessment.signal_id)
            existing_row = self._get_row(assessment.signal_id)
            if existing_effect is None and existing_row is not None:
                raise ValueError("PAPER_RISK_UNTRACKED_DURABLE_STATE")

            recorded_effect = self._effects.record(effect)
            if not recorded_effect:
                row = existing_row if existing_row is not None else self._get_row(assessment.signal_id)
                if row is None:
                    raise RuntimeError("PAPER_RISK_EFFECT_WITHOUT_ASSESSMENT")
                self._assert_row_matches(row, assessment)
                return False

            inserted_id = self._connection.execute(
                pg_insert(self._assessments)
                .values(
                    signal_id=assessment.signal_id,
                    decision=assessment.decision.value,
                    reason_code=assessment.reason_code,
                    approved_quantity=assessment.approved_quantity,
                )
                .on_conflict_do_nothing(index_elements=["signal_id"])
                .returning(self._assessments.c.signal_id)
            ).scalar_one_or_none()
            if inserted_id is None:
                row = self._get_row(assessment.signal_id)
                if row is not None:
                    self._assert_row_matches(row, assessment)
                raise ValueError("PAPER_RISK_DURABLE_STATE_CONFLICT")
            return True
