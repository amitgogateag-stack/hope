import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.domain.strategy.candidates import (
    StrategyCandidateClassification,
    StrategyCandidateState,
    StrategyMarket,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.strategy_candidates import (
    SqlAlchemyStrategyCandidateRepository,
)


@pytest.mark.integration
def test_strategy_candidate_registry_is_idempotent_and_append_only() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            strategy_id, strategy_version_id = uuid4(), uuid4()
            connection.execute(
                text(
                    "INSERT INTO strategies(strategy_id,name,family) "
                    "VALUES (:sid,:name,'TEST_FAMILY')"
                ),
                {"sid": strategy_id, "name": f"candidate-{strategy_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO strategy_versions("
                    "strategy_version_id,strategy_id,version,code_commit"
                    ") VALUES (:vid,:sid,'v1','candidate-commit')"
                ),
                {"vid": strategy_version_id, "sid": strategy_id},
            )

            definition = StrategyCandidateClassification(
                strategy_version_id=strategy_version_id,
                markets=frozenset({StrategyMarket.USA, StrategyMarket.INDIA}),
                state=StrategyCandidateState.RESEARCH,
                rationale="Initial dual-market research registration",
            )
            repository = SqlAlchemyStrategyCandidateRepository(connection)

            assert repository.append(definition) is True
            assert repository.append(definition) is False

            history = repository.history(strategy_version_id)
            assert len(history) == 1
            assert history[0].state is StrategyCandidateState.RESEARCH
            assert history[0].markets == (
                StrategyMarket.INDIA,
                StrategyMarket.USA,
            )

            latest_definition = StrategyCandidateClassification(
                strategy_version_id=strategy_version_id,
                markets=frozenset({StrategyMarket.USA}),
                state=StrategyCandidateState.RESEARCH,
                rationale="Narrowed current research scope to USA",
            )
            assert repository.append(latest_definition) is True

            history = repository.history(strategy_version_id)
            assert len(history) == 2
            assert history[0].classification_sequence < history[1].classification_sequence

            current = repository.current()
            assert len(current) == 1
            assert current[0].strategy_version_id == strategy_version_id
            assert current[0].family == "TEST_FAMILY"
            assert current[0].markets == (StrategyMarket.USA,)
            assert current[0].rationale == "Narrowed current research scope to USA"

            with pytest.raises(
                IntegrityError,
                match="STRATEGY_CANDIDATE_CLASSIFICATION_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE strategy_candidate_classifications "
                            "SET rationale='rewritten' "
                            "WHERE strategy_version_id=:vid"
                        ),
                        {"vid": strategy_version_id},
                    )

            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO strategy_candidate_classifications("
                            "classification_id,strategy_version_id,markets,state,rationale"
                            ") VALUES (:cid,:vid,ARRAY['USA'],'BACKUP_CANDIDATE','missing decision')"
                        ),
                        {"cid": uuid4(), "vid": strategy_version_id},
                    )
        finally:
            transaction.rollback()
