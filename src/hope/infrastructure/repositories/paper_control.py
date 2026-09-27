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
            Column("actor", String, nullable=False),
            Column("database_principal", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def current_state(self) -> str:
        state = self._connection.execute(
            select(self._events.c.state)
            .order_by(self._events.c.control_sequence.desc())
            .limit(1)
        ).scalar_one_or_none()
        if state is None:
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_MISSING")
        if state not in {"RUNNING", "HALTED"}:
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_INVALID")
        return state

    def _assert_sequence_generator_ready(self) -> None:
        row = self._connection.execute(
            text(
                "SELECT "
                "(SELECT max(control_sequence) "
                "FROM paper_environment_control_events) AS history_max, "
                "last_value AS generator_last, "
                "is_called AS generator_is_called "
                "FROM paper_environment_control_events_control_sequence_seq"
            )
        ).mappings().one()
        history_max = row["history_max"]
        generator_last = row["generator_last"]
        generator_is_called = row["generator_is_called"]
        if history_max is None:
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_MISSING")
        if (
            generator_last < history_max
            or (generator_last == history_max and generator_is_called is not True)
        ):
            raise RuntimeError(
                "PAPER_ENVIRONMENT_CONTROL_SEQUENCE_GENERATOR_INVALID"
            )

    def assert_running(self) -> None:
        self._assert_sequence_generator_ready()
        state = self.current_state()
        if state != "RUNNING":
            if state == "HALTED":
                raise RuntimeError("PAPER_ENVIRONMENT_HALTED")
            raise RuntimeError("PAPER_ENVIRONMENT_CONTROL_STATE_INVALID")


    def transition(
        self,
        expected_state: str,
        new_state: str,
        reason: str,
        *,
        actor: str,
    ) -> int:
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
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("PAPER_ENVIRONMENT_CONTROL_ACTOR_REQUIRED")
        if actor != actor.strip():
            raise ValueError("PAPER_ENVIRONMENT_CONTROL_ACTOR_NOT_CANONICAL")

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

        if expected_state == "HALTED" and new_state == "RUNNING":
            incomplete_claim_exists = self._connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM job_runs "
                    "WHERE status = 'CLAIMED' AND left(job_key, 6) = 'paper:'"
                    ")"
                )
            ).scalar_one()
            if incomplete_claim_exists is True:
                raise RuntimeError(
                    "PAPER_ENVIRONMENT_RESUME_BLOCKED_BY_INCOMPLETE_CLAIM"
                )

        sequence = self._connection.execute(
            insert(self._events)
            .values(state=new_state, reason=reason, actor=actor)
            .returning(self._events.c.control_sequence)
        ).scalar_one()
        return int(sequence)
