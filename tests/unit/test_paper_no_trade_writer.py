from datetime import datetime, timezone

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffectType
from hope.application.paper.no_trade import PaperNoTradeWriter


class _Repository:
    def __init__(self) -> None:
        self.calls = []

    def persist(self, effect, decision):
        self.calls.append((effect, decision))
        return True


def test_no_trade_writer_creates_recoverable_job_owned_effect() -> None:
    scheduled_for = datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run("paper:no-trade", scheduled_for)
    repository = _Repository()

    assert PaperNoTradeWriter(repository).record(
        PaperCycleContext(run),
        strategy_version="strategy-v1",
        decision_time=scheduled_for,
        inputs_hash="a" * 64,
    ) is True

    effect, decision = repository.calls[0]
    assert effect.effect_type is PaperEffectType.NO_TRADE
    assert effect.entity_id == run.job_run_id
    assert effect.job_run_id == run.job_run_id
    assert len(effect.payload_hash) == 64
    assert effect.payload_hash != decision.inputs_hash
    assert decision.decision_id == run.job_run_id


def test_no_trade_writer_rejects_non_scheduled_decision() -> None:
    scheduled_for = datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run("paper:no-trade", scheduled_for)
    repository = _Repository()

    with pytest.raises(ValueError, match="PAPER_NO_TRADE_DECISION_TIME_MISMATCH"):
        PaperNoTradeWriter(repository).record(
            PaperCycleContext(run),
            strategy_version="strategy-v1",
            decision_time=scheduled_for.replace(minute=1),
            inputs_hash="a" * 64,
        )

    assert repository.calls == []
