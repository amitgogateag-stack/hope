from __future__ import annotations

from sqlalchemy import Connection, MetaData, Table, Column, BigInteger, String, DateTime, select


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
