from __future__ import annotations

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.no_trade import PaperNoTradeDecision, paper_no_trade_payload_hash
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperNoTradeRepository:
    """Atomically persist an explicit no-trade outcome and its recovery effect."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._decisions = Table(
            "paper_no_trade_decisions",
            metadata,
            Column("decision_id", Uuid, primary_key=True),
            Column("job_run_id", Uuid, nullable=False),
            Column("strategy_version", String, nullable=False),
            Column("decision_time", DateTime(timezone=True), nullable=False),
            Column("inputs_hash", String(64), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _get(self, decision_id):
        return self._connection.execute(
            select(self._decisions).where(self._decisions.c.decision_id == decision_id)
        ).mappings().one_or_none()

    @staticmethod
    def _assert_matches(row, decision: PaperNoTradeDecision) -> None:
        if (
            row["decision_id"] != decision.decision_id
            or row["job_run_id"] != decision.decision_id
            or row["strategy_version"] != decision.strategy_version
            or row["decision_time"] != decision.decision_time
            or row["inputs_hash"] != decision.inputs_hash
        ):
            raise ValueError("PAPER_NO_TRADE_IDENTITY_CONFLICT")

    def persist(self, effect: PaperEffect, decision: PaperNoTradeDecision) -> bool:
        if (
            effect.effect_type is not PaperEffectType.NO_TRADE
            or effect.entity_id != decision.decision_id
            or effect.job_run_id != decision.decision_id
        ):
            raise ValueError("PAPER_NO_TRADE_EFFECT_MISMATCH")
        if effect.payload_hash != paper_no_trade_payload_hash(decision):
            raise ValueError("PAPER_NO_TRADE_EFFECT_PAYLOAD_MISMATCH")

        with self._connection.begin_nested():
            existing_effect = self._effects.get(PaperEffectType.NO_TRADE, decision.decision_id)
            existing_decision = self._get(decision.decision_id)
            if existing_effect is None and existing_decision is not None:
                raise ValueError("PAPER_NO_TRADE_UNTRACKED_DURABLE_STATE")

            recorded_effect = self._effects.record(effect)
            if not recorded_effect:
                row = existing_decision or self._get(decision.decision_id)
                if row is None:
                    raise RuntimeError("PAPER_NO_TRADE_EFFECT_WITHOUT_DECISION")
                self._assert_matches(row, decision)
                return False

            inserted = self._connection.execute(
                pg_insert(self._decisions)
                .values(
                    decision_id=decision.decision_id,
                    job_run_id=decision.decision_id,
                    strategy_version=decision.strategy_version,
                    decision_time=decision.decision_time,
                    inputs_hash=decision.inputs_hash,
                )
                .on_conflict_do_nothing(index_elements=["decision_id"])
                .returning(self._decisions.c.decision_id)
            ).scalar_one_or_none()
            if inserted is None:
                row = self._get(decision.decision_id)
                if row is not None:
                    self._assert_matches(row, decision)
                raise ValueError("PAPER_NO_TRADE_DURABLE_STATE_CONFLICT")
            return True
