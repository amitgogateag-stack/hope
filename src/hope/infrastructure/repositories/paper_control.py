from __future__ import annotations

from sqlalchemy import Connection, MetaData, Table, Column, BigInteger, String, DateTime, insert, select, text


class SqlAlchemyPaperEnvironmentControlRepository:
    """Read the append-only global PAPER execution control state."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._events = Table(
            "paper_environment_control_events",
            metadata,
            Column("control_sequence", BigInteger, primary_key=True),
            Column("state", String, nullable=False),
            Column("reason", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def current_state(self) -> str:
        row = self._connection.execute(
            select(self._events.c.state)
            .order_by(self._events.c.control_sequence.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_MISSING")
        return row

    def assert_running(self) -> None:
        state = self.current_state()
        if state != "RUNNING":
            if state == "HALTED":
                raise RuntimeError("PAPER_ENVIRONMENT_HALTED")
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_INVALID")


    def transition(self, expected_state: str, new_state: str, reason: str) -> int:
        """Append one serialized control transition and reject stale/operator-invalid writes."""
        valid_states = {"RUNNING", "HALTED"}
        if expected_state not in valid_states or new_state not in valid_states:
            raise ValueError("PAPER_ENVIRONMENT_CONTROL_STATE_UNSUPPORTED")
        if expected_state == new_state:
            raise ValueError("PAPER_ENVIRONMENT_CONTROL_NOOP_TRANSITION")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("PAPER_ENVIRONMENT_CONTROL_REASON_REQUIRED")
        if reason != reason.strip():
            raise ValueError("PAPER_ENVIRONMENT_CONTROL_REASON_NOT_CANONICAL")

        self._connection.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('hope:paper:environment-control')::bigint)"
            )
        )
        current = self._connection.execute(
            select(self._events.c.state)
            .order_by(self._events.c.control_sequence.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if current is None:
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_MISSING")
        if current != expected_state:
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_TRANSITION_CONFLICT")

        sequence = self._connection.execute(
            insert(self._events)
            .values(state=new_state, reason=reason)
            .returning(self._events.c.control_sequence)
        ).scalar_one()
        return int(sequence)
