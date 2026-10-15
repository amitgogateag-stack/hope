from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.no_trade import PaperNoTradeDecision, paper_no_trade_payload_hash
from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository


class _Result:
    def __init__(self, *, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.scalar


class _Connection:
    def __init__(self, *results):
        self.results = list(results)

    def begin_nested(self):
        return nullcontext()

    def execute(self, statement):
        assert statement is not None
        return self.results.pop(0)


class _Effects:
    def __init__(self, existing, recorded):
        self.existing = existing
        self.recorded = recorded
        self.record_calls = []

    def get(self, effect_type, entity_id):
        assert effect_type is PaperEffectType.NO_TRADE
        return self.existing

    def record(self, effect):
        self.record_calls.append(effect)
        return self.recorded


def _decision_and_effect():
    scheduled_for = datetime(2026, 10, 15, 13, tzinfo=UTC)
    run = create_scheduled_job_run("paper:no-trade", scheduled_for)
    decision = PaperNoTradeDecision(
        decision_id=run.job_run_id,
        strategy_version="strategy-v1",
        decision_time=scheduled_for,
        inputs_hash="a" * 64,
    )
    effect = create_paper_effect(
        run,
        PaperEffectType.NO_TRADE,
        decision.decision_id,
        paper_no_trade_payload_hash(decision),
    )
    return decision, effect


def _row(decision):
    return {
        "decision_id": decision.decision_id,
        "job_run_id": decision.decision_id,
        "strategy_version": decision.strategy_version,
        "decision_time": decision.decision_time,
        "inputs_hash": decision.inputs_hash,
    }


def _repository(connection, effects):
    repository = SqlAlchemyPaperNoTradeRepository(connection)
    repository._effects = effects
    return repository


def test_persist_inserts_effect_and_durable_decision_atomically():
    decision, effect = _decision_and_effect()
    effects = _Effects(None, True)
    repository = _repository(
        _Connection(_Result(row=None), _Result(scalar=decision.decision_id)),
        effects,
    )

    assert repository.persist(effect, decision) is True
    assert effects.record_calls == [effect]


def test_persist_reuses_matching_effect_and_decision():
    decision, effect = _decision_and_effect()
    effects = _Effects(effect, False)
    repository = _repository(_Connection(_Result(row=_row(decision))), effects)

    assert repository.persist(effect, decision) is False


def test_persist_rejects_untracked_durable_decision():
    decision, effect = _decision_and_effect()
    effects = _Effects(None, True)
    repository = _repository(_Connection(_Result(row=_row(decision))), effects)

    with pytest.raises(ValueError, match="PAPER_NO_TRADE_UNTRACKED_DURABLE_STATE"):
        repository.persist(effect, decision)
    assert effects.record_calls == []


def test_persist_rejects_effect_without_decision():
    decision, effect = _decision_and_effect()
    effects = _Effects(effect, False)
    repository = _repository(
        _Connection(_Result(row=None), _Result(row=None)),
        effects,
    )

    with pytest.raises(RuntimeError, match="PAPER_NO_TRADE_EFFECT_WITHOUT_DECISION"):
        repository.persist(effect, decision)


def test_persist_rejects_conflicting_insert_even_when_row_matches():
    decision, effect = _decision_and_effect()
    effects = _Effects(None, True)
    repository = _repository(
        _Connection(
            _Result(row=None),
            _Result(scalar=None),
            _Result(row=_row(decision)),
        ),
        effects,
    )

    with pytest.raises(ValueError, match="PAPER_NO_TRADE_DURABLE_STATE_CONFLICT"):
        repository.persist(effect, decision)


def test_persist_rejects_mismatched_effect_identity_and_payload():
    decision, effect = _decision_and_effect()
    repository = object.__new__(SqlAlchemyPaperNoTradeRepository)
    wrong_type = create_paper_effect(
        create_scheduled_job_run("paper:no-trade", decision.decision_time),
        PaperEffectType.SIGNAL,
        decision.decision_id,
        effect.payload_hash,
    )
    with pytest.raises(ValueError, match="PAPER_NO_TRADE_EFFECT_MISMATCH"):
        repository.persist(wrong_type, decision)

    wrong_payload = replace(effect, payload_hash="b" * 64)
    with pytest.raises(ValueError, match="PAPER_NO_TRADE_EFFECT_PAYLOAD_MISMATCH"):
        repository.persist(wrong_payload, decision)


def test_identity_match_rejects_any_durable_field_change():
    decision, _ = _decision_and_effect()
    row = _row(decision)
    row["strategy_version"] = "rewritten"

    with pytest.raises(ValueError, match="PAPER_NO_TRADE_IDENTITY_CONFLICT"):
        SqlAlchemyPaperNoTradeRepository._assert_matches(row, decision)
