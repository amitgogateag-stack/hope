from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.signals import paper_signal_payload_hash
from hope.domain.signal.models import Signal
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperSignalRepository:
    """Persist PAPER signals together with their durable idempotency evidence."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._signals = Table(
            "signals",
            metadata,
            Column("signal_id", Uuid, primary_key=True),
            Column("experiment_id", String, nullable=True),
            Column("instrument_id", Uuid, nullable=False),
            Column("decision_time", DateTime(timezone=True), nullable=False),
            Column("state", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _get_row(self, signal_id: UUID):
        return self._connection.execute(
            select(
                self._signals.c.signal_id,
                self._signals.c.instrument_id,
                self._signals.c.decision_time,
                self._signals.c.state,
            ).where(self._signals.c.signal_id == signal_id)
        ).mappings().one_or_none()

    @staticmethod
    def _assert_signal_row_matches(row, signal: Signal) -> None:
        if (
            row["signal_id"] != signal.signal_id
            or row["instrument_id"] != signal.instrument_id
            or row["decision_time"] != signal.decision_time
            or row["state"] != "SIGNAL"
        ):
            raise ValueError("PAPER_SIGNAL_IDENTITY_CONFLICT")

    def persist(self, effect: PaperEffect, signal: Signal) -> bool:
        """Atomically persist a PAPER signal and its effect within a savepoint."""
        if effect.effect_type is not PaperEffectType.SIGNAL or effect.entity_id != signal.signal_id:
            raise ValueError("PAPER_SIGNAL_EFFECT_MISMATCH")
        if effect.payload_hash != paper_signal_payload_hash(signal):
            raise ValueError("PAPER_SIGNAL_EFFECT_PAYLOAD_MISMATCH")

        with self._connection.begin_nested():
            existing_effect = self._effects.get(PaperEffectType.SIGNAL, signal.signal_id)
            existing_signal = self._get_row(signal.signal_id)
            if existing_effect is None and existing_signal is not None:
                raise ValueError("PAPER_SIGNAL_UNTRACKED_DURABLE_STATE")

            recorded_effect = self._effects.record(effect)
            if not recorded_effect:
                row = existing_signal if existing_signal is not None else self._get_row(signal.signal_id)
                if row is None:
                    raise RuntimeError("PAPER_SIGNAL_EFFECT_WITHOUT_SIGNAL")
                self._assert_signal_row_matches(row, signal)
                return False

            inserted_id = self._connection.execute(
                pg_insert(self._signals)
                .values(
                    signal_id=signal.signal_id,
                    experiment_id=None,
                    instrument_id=signal.instrument_id,
                    decision_time=signal.decision_time,
                    state="SIGNAL",
                )
                .on_conflict_do_nothing(index_elements=["signal_id"])
                .returning(self._signals.c.signal_id)
            ).scalar_one_or_none()
            if inserted_id is None:
                row = self._get_row(signal.signal_id)
                if row is not None:
                    self._assert_signal_row_matches(row, signal)
                raise ValueError("PAPER_SIGNAL_DURABLE_STATE_CONFLICT")
            return True
