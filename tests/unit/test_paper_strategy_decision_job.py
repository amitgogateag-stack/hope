from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.jobs import PaperStrategyDecisionJob
from hope.application.paper.provenance import paper_decision_inputs_hash
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.signal.models import Signal, SignalType
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.domain.universe.models import UniverseMember, UniverseVersion


UTC = timezone.utc


class _Runtime:
    def __init__(self, job_run) -> None:
        self.cycle = PaperCycleContext(job_run)
        self.recorded = []

    def record_signal(self, signal: Signal) -> bool:
        self.recorded.append(signal)
        return True


class _Parameters(ParameterSnapshot):
    pass


class _TunedParameters(ParameterSnapshot):
    lookback: int


class _Strategy(Strategy):
    name = "test-strategy"
    version = "test-strategy-v1"

    def __init__(self, signals=()) -> None:
        self.signals = signals
        self.calls = []

    def generate_signals(self, market_context, universe, parameters):
        self.calls.append((market_context, universe, parameters))
        return self.signals


class _StrategyV2(_Strategy):
    version = "test-strategy-v2"


def _version(*, pit_certified: bool = True, version: str = "u1") -> UniverseVersion:
    return UniverseVersion(
        universe_id=uuid4(),
        version=version,
        declared_member_count=1,
        pit_certified=pit_certified,
    )


def _snapshot(*, pit_certified: bool = True, version: str = "u1") -> UniverseSnapshot:
    return UniverseSnapshot(
        uuid4(),
        _version(pit_certified=pit_certified, version=version),
        (UniverseMember(instrument_id=uuid4()),),
    )


def _signal(
    decision_time: datetime,
    *,
    version: str = "test-strategy-v1",
    inputs_hash: str = "b" * 64,
) -> Signal:
    return Signal(
        signal_id=uuid4(),
        instrument_id=uuid4(),
        strategy_version=version,
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.6"),
        inputs_hash=inputs_hash,
    )


def _runtime(scheduled_for: datetime) -> _Runtime:
    return _Runtime(create_scheduled_job_run("paper-strategy-decision", scheduled_for))


def test_paper_decision_inputs_hash_is_deterministic() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    strategy = _Strategy()
    context = PITMarketContext(as_of=decision_time, bars=())
    universe = _snapshot()
    parameters = _TunedParameters(lookback=20)

    first = paper_decision_inputs_hash(strategy, context, universe, parameters)
    second = paper_decision_inputs_hash(strategy, context, universe, parameters)

    assert first == second
    assert len(first) == 64


def test_paper_decision_inputs_hash_changes_with_decision_inputs() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    strategy = _Strategy()
    context = PITMarketContext(as_of=decision_time, bars=())
    universe = _snapshot()
    parameters = _TunedParameters(lookback=20)
    baseline = paper_decision_inputs_hash(strategy, context, universe, parameters)

    assert paper_decision_inputs_hash(
        strategy,
        PITMarketContext(as_of=decision_time + timedelta(seconds=1), bars=()),
        universe,
        parameters,
    ) != baseline
    assert paper_decision_inputs_hash(_StrategyV2(), context, universe, parameters) != baseline
    assert paper_decision_inputs_hash(
        strategy,
        context,
        UniverseSnapshot(uuid4(), universe.version, universe.members),
        parameters,
    ) != baseline
    changed_member = UniverseMember(instrument_id=uuid4())
    assert paper_decision_inputs_hash(
        strategy,
        context,
        UniverseSnapshot(universe.universe_version_id, universe.version, (changed_member,)),
        parameters,
    ) != baseline
    assert paper_decision_inputs_hash(
        strategy, context, universe, _TunedParameters(lookback=21)
    ) != baseline


def test_paper_strategy_decision_job_requires_universe_snapshot() -> None:
    strategy = _Strategy(())
    context = PITMarketContext(as_of=datetime(2026, 9, 10, 14, 0, tzinfo=UTC), bars=())

    with pytest.raises(TypeError, match="PAPER_DECISION_JOB_REQUIRES_UNIVERSE_SNAPSHOT"):
        PaperStrategyDecisionJob(strategy, context, _version(), _Parameters())


def test_paper_strategy_decision_job_requires_pit_certified_universe() -> None:
    strategy = _Strategy(())
    context = PITMarketContext(as_of=datetime(2026, 9, 10, 14, 0, tzinfo=UTC), bars=())

    with pytest.raises(ValueError, match="PAPER_DECISION_JOB_REQUIRES_PIT_CERTIFIED_UNIVERSE"):
        PaperStrategyDecisionJob(strategy, context, _snapshot(pit_certified=False), _Parameters())


def test_paper_strategy_decision_job_records_valid_strategy_signals() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=decision_time, bars=())
    universe = _snapshot()
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(strategy, context, universe, parameters)
    signal = _signal(decision_time, inputs_hash=inputs_hash)
    strategy.signals = (signal,)
    runtime = _runtime(decision_time)

    PaperStrategyDecisionJob(strategy, context, universe, parameters)(runtime)

    assert strategy.calls == [(context, universe.version, parameters)]
    assert runtime.recorded == [signal]


def test_paper_strategy_decision_job_allows_explicit_no_trade() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=decision_time, bars=())
    strategy = _Strategy(())
    runtime = _runtime(decision_time)

    PaperStrategyDecisionJob(strategy, context, _snapshot(), _Parameters())(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_context_after_schedule_before_strategy_runs() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=scheduled_for + timedelta(seconds=1), bars=())
    strategy = _Strategy(())
    runtime = _runtime(scheduled_for)

    with pytest.raises(ValueError, match="PAPER_DECISION_AFTER_JOB_SCHEDULE"):
        PaperStrategyDecisionJob(strategy, context, _snapshot(), _Parameters())(runtime)

    assert strategy.calls == []
    assert runtime.recorded == []


def test_paper_strategy_decision_job_validates_all_outputs_before_persisting() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=decision_time, bars=())
    universe = _snapshot()
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(strategy, context, universe, parameters)
    valid = _signal(decision_time, inputs_hash=inputs_hash)
    invalid = _signal(decision_time + timedelta(seconds=1), inputs_hash=inputs_hash)
    strategy.signals = (valid, invalid)
    runtime = _runtime(decision_time + timedelta(minutes=1))

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_DECISION_TIME_MISMATCH"):
        PaperStrategyDecisionJob(strategy, context, universe, parameters)(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_non_tuple_output() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=decision_time, bars=())
    strategy = _Strategy([])
    runtime = _runtime(decision_time)

    with pytest.raises(TypeError, match="PAPER_STRATEGY_SIGNALS_MUST_BE_TUPLE"):
        PaperStrategyDecisionJob(strategy, context, _snapshot(), _Parameters())(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_strategy_version_mismatch() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=decision_time, bars=())
    universe = _snapshot()
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(strategy, context, universe, parameters)
    strategy.signals = (_signal(decision_time, version="wrong-version", inputs_hash=inputs_hash),)
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_VERSION_MISMATCH"):
        PaperStrategyDecisionJob(strategy, context, universe, parameters)(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_inputs_hash_mismatch_without_persisting() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    context = PITMarketContext(as_of=decision_time, bars=())
    universe = _snapshot()
    parameters = _TunedParameters(lookback=20)
    strategy = _Strategy((_signal(decision_time, inputs_hash="f" * 64),))
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_INPUTS_HASH_MISMATCH"):
        PaperStrategyDecisionJob(strategy, context, universe, parameters)(runtime)

    assert runtime.recorded == []
