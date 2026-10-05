import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_reconciliation_receipt_is_unique_per_authenticated_job_run() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    strategy_id = uuid4()
    strategy_version_id = uuid4()
    job_run_id = uuid4()
    effect_rows = [
        {
            "effect_id": uuid4(),
            "effect_type": effect_type,
            "entity_id": uuid4(),
            "payload_hash": "a" * 64,
        }
        for effect_type in ("SIGNAL", "RISK", "ORDER", "REJECTION")
    ]
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"),
                {"id": strategy_id, "name": f"RECONCILIATION_AUDIT_{strategy_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                    "VALUES (:version_id, :strategy_id, 'v1', 'reconciliation-audit-test')"
                ),
                {"version_id": strategy_version_id, "strategy_id": strategy_id},
            )
            connection.execute(
                text(
                    "INSERT INTO job_runs(job_run_id, job_key, scheduled_for, created_at) "
                    "VALUES (:id, :key, transaction_timestamp(), transaction_timestamp())"
                ),
                {
                    "id": job_run_id,
                    "key": f"paper:USA:{strategy_version_id}:reconciliation-audit-test",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO paper_effects("
                    "effect_id, job_run_id, effect_type, entity_id, payload_hash"
                    ") VALUES (:effect_id, :job_run_id, :effect_type, :entity_id, :payload_hash)"
                ),
                [
                    {**effect_row, "job_run_id": job_run_id}
                    for effect_row in effect_rows
                ],
            )
            connection.execute(
                text(
                    "UPDATE job_runs SET status='SUCCEEDED', completed_at=transaction_timestamp() "
                    "WHERE job_run_id=:id"
                ),
                {"id": job_run_id},
            )

            malformed_receipt = (
                "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                "VALUES (:id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', :entity_id, '{}'::JSONB)"
            )
            with pytest.raises(
                IntegrityError,
                match="PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(malformed_receipt),
                        {"id": uuid4(), "entity_id": str(job_run_id)},
                    )

            forged_effect_receipt = (
                "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                "SELECT :id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', job_run_id::text, "
                "jsonb_build_object("
                "'schema_version', 1, "
                "'decision', 'ACKNOWLEDGE_COMPLETE_EFFECTS', "
                "'job_key', job_key, "
                "'scheduled_for', scheduled_for::text, "
                "'completed_at', completed_at::text, "
                "'effects', jsonb_build_array(jsonb_build_object("
                "'effect_id', '00000000-0000-0000-0000-000000000901', "
                "'effect_type', 'SIGNAL', "
                "'entity_id', '00000000-0000-0000-0000-000000000902', "
                "'payload_hash', repeat('0', 64)"
                "))"
                ") FROM job_runs WHERE job_run_id=:job_run_id"
            )
            with pytest.raises(
                IntegrityError,
                match="PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(forged_effect_receipt),
                        {"id": uuid4(), "job_run_id": job_run_id},
                    )

            receipt = (
                "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                "SELECT :id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', jr.job_run_id::text, "
                "jsonb_build_object("
                "'schema_version', 1, "
                "'decision', 'ACKNOWLEDGE_COMPLETE_EFFECTS', "
                "'job_key', jr.job_key, "
                "'scheduled_for', jr.scheduled_for::text, "
                "'completed_at', jr.completed_at::text, "
                "'effects', ("
                "SELECT jsonb_agg(jsonb_build_object("
                "'effect_id', pe.effect_id::text, "
                "'effect_type', pe.effect_type, "
                "'entity_id', pe.entity_id::text, "
                "'payload_hash', pe.payload_hash"
                ") ORDER BY pe.created_at, pe.effect_id) "
                "FROM paper_effects pe WHERE pe.job_run_id=jr.job_run_id"
                ")"
                ") FROM job_runs jr WHERE jr.job_run_id=:job_run_id"
            )
            reordered_receipt = receipt.replace(
                "ORDER BY pe.created_at, pe.effect_id",
                "ORDER BY pe.created_at DESC, pe.effect_id DESC",
            )
            with pytest.raises(
                IntegrityError,
                match="PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(reordered_receipt),
                        {"id": uuid4(), "job_run_id": job_run_id},
                    )

            forged_timestamp_receipt = receipt.replace(
                "entity_id, payload) ",
                "entity_id, payload, created_at) ",
            ).replace(
                ") FROM job_runs jr WHERE jr.job_run_id=:job_run_id",
                "), transaction_timestamp() - interval '1 microsecond' "
                "FROM job_runs jr WHERE jr.job_run_id=:job_run_id",
            )
            with pytest.raises(
                IntegrityError,
                match="PAPER_RECONCILIATION_AUDIT_TIMESTAMP_NOT_DATABASE_AUTHENTICATED",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(forged_timestamp_receipt),
                        {"id": uuid4(), "job_run_id": job_run_id},
                    )

            connection.execute(
                text(receipt),
                {"id": uuid4(), "job_run_id": job_run_id},
            )

            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(receipt),
                        {"id": uuid4(), "job_run_id": job_run_id},
                    )

            count = connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE event_type = 'PAPER_RUN_RECONCILED' "
                    "AND entity_type = 'JOB_RUN' AND entity_id = :entity_id"
                ),
                {"entity_id": str(job_run_id)},
            ).scalar_one()
            assert count == 1

            partial_job_run_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO job_runs(job_run_id, job_key, scheduled_for, created_at) "
                    "VALUES (:id, :key, transaction_timestamp(), transaction_timestamp())"
                ),
                {
                    "id": partial_job_run_id,
                    "key": f"paper:USA:{strategy_version_id}:reconciliation-audit-partial",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO paper_effects("
                    "effect_id, job_run_id, effect_type, entity_id, payload_hash"
                    ") VALUES (:effect_id, :job_run_id, 'SIGNAL', :entity_id, :payload_hash)"
                ),
                {
                    "effect_id": uuid4(),
                    "job_run_id": partial_job_run_id,
                    "entity_id": uuid4(),
                    "payload_hash": "b" * 64,
                },
            )
            connection.execute(
                text(
                    "UPDATE job_runs SET status='SUCCEEDED', completed_at=transaction_timestamp() "
                    "WHERE job_run_id=:id"
                ),
                {"id": partial_job_run_id},
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(receipt),
                        {"id": uuid4(), "job_run_id": partial_job_run_id},
                    )
        finally:
            transaction.rollback()


@pytest.mark.integration
@pytest.mark.parametrize("entity_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000099"])
def test_paper_reconciliation_receipt_rejects_unproven_job_identity(entity_id: str) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        with pytest.raises(IntegrityError, match="PAPER_RECONCILIATION_AUDIT_JOB_IDENTITY_INVALID"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                        "VALUES (:id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', :entity_id, '{}'::JSONB)"
                    ),
                    {"id": uuid4(), "entity_id": entity_id},
                )


@pytest.mark.integration
def test_paper_reconciliation_receipt_rejects_non_paper_job_identity() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        job_run_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO job_runs(job_run_id, job_key, scheduled_for, created_at) "
                "VALUES (:id, 'research:test', transaction_timestamp(), transaction_timestamp())"
            ),
            {"id": job_run_id},
        )
        with pytest.raises(IntegrityError, match="PAPER_RECONCILIATION_AUDIT_JOB_IDENTITY_INVALID"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                        "VALUES (:id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', :entity_id, '{}'::JSONB)"
                    ),
                    {"id": uuid4(), "entity_id": str(job_run_id)},
                )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("status", "completed", "failure_code"),
    [
        ("CLAIMED", False, None),
        ("FAILED", True, "RECONCILIATION_TEST_FAILURE"),
    ],
)
def test_paper_reconciliation_receipt_requires_clean_succeeded_terminal_truth(
    status: str, completed: bool, failure_code: str | None
) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            job_run_id = uuid4()
            connection.execute(
                text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"),
                {"id": strategy_id, "name": f"RECONCILIATION_TERMINAL_{strategy_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                    "VALUES (:version_id, :strategy_id, 'v1', 'reconciliation-terminal-test')"
                ),
                {"version_id": strategy_version_id, "strategy_id": strategy_id},
            )
            connection.execute(
                text(
                    "INSERT INTO job_runs(job_run_id, job_key, scheduled_for, created_at) "
                    "VALUES (:id, :key, transaction_timestamp(), transaction_timestamp())"
                ),
                {
                    "id": job_run_id,
                    "key": f"paper:USA:{strategy_version_id}:reconciliation-terminal-test",
                },
            )
            if status != "CLAIMED":
                connection.execute(
                    text(
                        "UPDATE job_runs SET status=:status, "
                        "completed_at=CASE WHEN :completed THEN transaction_timestamp() ELSE NULL END, "
                        "failure_code=:failure_code WHERE job_run_id=:id"
                    ),
                    {
                        "status": status,
                        "completed": completed,
                        "failure_code": failure_code,
                        "id": job_run_id,
                    },
                )

            with pytest.raises(
                IntegrityError,
                match="PAPER_RECONCILIATION_AUDIT_TERMINAL_TRUTH_INVALID",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                            "VALUES (:id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', :entity_id, '{}'::JSONB)"
                        ),
                        {"id": uuid4(), "entity_id": str(job_run_id)},
                    )
        finally:
            transaction.rollback()
            engine.dispose()

@pytest.mark.integration
def test_paper_reconciliation_receipt_rejects_non_job_run_entity_type() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        with pytest.raises(
            IntegrityError,
            match="PAPER_RECONCILIATION_AUDIT_ENTITY_TYPE_INVALID",
        ):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO audit_events("
                        "audit_event_id, event_type, entity_type, entity_id, payload"
                        ") VALUES ("
                        ":id, 'PAPER_RUN_RECONCILED', 'ORDER', :entity_id, '{}'::JSONB"
                        ")"
                    ),
                    {"id": uuid4(), "entity_id": str(uuid4())},
                )

