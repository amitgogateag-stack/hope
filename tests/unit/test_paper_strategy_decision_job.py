from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.jobs import PaperStrategyDecisionJob
from hope.application.paper.provenance import paper_decision_inputs_hash
from hope.application.paper.runner import PaperRuntimeContext
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.domain.signal.models import Signal, SignalType
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.paper_runtime import PaperJobDefinition


UTC = timezone.utc
FRESHNESS_MAX_AGE = timedelta(minutes=5)
BOUND_DATASET_VERSION_ID = UUID("00000000-0000-0000-0000-000000000001")


class _Runtime:
    def __init__(self, job_run) -> None:
        self.cycle = PaperCycleContext(job_run)
        self.recorded = []
        self.no_trade = []

    def record_signal(self, signal: Signal) -> bool:
        self.recorded.append(signal)
        return True

    def record_no_trade(self, **outcome) -> bool:
        self.no_trade.append(outcome)
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

    def generate_signals(self, market_context, universe, universe_members, parameters, inputs_hash):
        self.calls.append((market_context, universe, universe_members, parameters, inputs_hash))
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
    instrument_id=None,
    version: str = "test-strategy-v1",
    inputs_hash: str = "b" * 64,
) -> Signal:
    return Signal(
        signal_id=uuid4(),
        instrument_id=instrument_id or uuid4(),
        strategy_version=version,
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.6"),
        inputs_hash=inputs_hash,
    )


def _runtime(scheduled_for: datetime) -> _Runtime:
    return _Runtime(create_scheduled_job_run("paper-strategy-decision", scheduled_for))


def _context(
    as_of: datetime,
    universe: UniverseSnapshot,
    *,
    age: timedelta = timedelta(0),
) -> PITMarketContext:
    bars = tuple(
        MarketBar(
            instrument_id=str(member.instrument_id),
            event_time=as_of - age,
            available_time=as_of - age,
            ingestion_time=as_of - age,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("100"),
        )
        for member in universe.active_members(as_of)
    )
    return PITMarketContext(as_of=as_of, bars=bars)


def _durably_bound_decision(
    strategy: Strategy,
    context: PITMarketContext,
    universe: UniverseSnapshot,
    parameters: ParameterSnapshot,
    max_age: timedelta = FRESHNESS_MAX_AGE,
) -> PaperStrategyDecisionJob:
    return PaperStrategyDecisionJob._from_durable_binding(
        strategy,
        context,
        universe,
        parameters,
        max_age,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
    )


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
    assert paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        market_data_max_age=timedelta(minutes=5),
    ) != paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        market_data_max_age=timedelta(minutes=6),
    )


@pytest.mark.parametrize(
    ("max_age", "error_code"),
    (
        ("5 minutes", "PAPER_DECISION_INPUTS_REQUIRES_FRESHNESS_TIMEDELTA"),
        (timedelta(0), "PAPER_DECISION_INPUTS_REQUIRES_POSITIVE_FRESHNESS"),
    ),
)
def test_paper_decision_inputs_hash_rejects_invalid_freshness_policy(
    max_age,
    error_code: str,
) -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)

    with pytest.raises((TypeError, ValueError), match=error_code):
        paper_decision_inputs_hash(
            _Strategy(),
            PITMarketContext(as_of=decision_time, bars=()),
            _snapshot(),
            _TunedParameters(lookback=20),
            market_data_max_age=max_age,
        )


def test_paper_strategy_decision_job_requires_universe_snapshot() -> None:
    strategy = _Strategy(())
    context = PITMarketContext(as_of=datetime(2026, 9, 10, 14, 0, tzinfo=UTC), bars=())

    with pytest.raises(TypeError, match="PAPER_DECISION_JOB_REQUIRES_UNIVERSE_SNAPSHOT"):
        PaperStrategyDecisionJob(
            strategy, context, _version(), _Parameters(), FRESHNESS_MAX_AGE
        )


def test_paper_strategy_decision_job_requires_pit_certified_universe() -> None:
    strategy = _Strategy(())
    context = PITMarketContext(as_of=datetime(2026, 9, 10, 14, 0, tzinfo=UTC), bars=())

    with pytest.raises(ValueError, match="PAPER_DECISION_JOB_REQUIRES_PIT_CERTIFIED_UNIVERSE"):
        PaperStrategyDecisionJob(
            strategy,
            context,
            _snapshot(pit_certified=False),
            _Parameters(),
            FRESHNESS_MAX_AGE,
        )


@pytest.mark.parametrize(
    ("max_age", "error_code"),
    (
        ("5 minutes", "PAPER_DECISION_JOB_REQUIRES_FRESHNESS_TIMEDELTA"),
        (timedelta(0), "PAPER_DECISION_JOB_REQUIRES_POSITIVE_FRESHNESS"),
    ),
)
def test_paper_strategy_decision_job_requires_valid_freshness_policy(
    max_age,
    error_code: str,
) -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()

    with pytest.raises((TypeError, ValueError), match=error_code):
        PaperStrategyDecisionJob(
            _Strategy(),
            _context(decision_time, universe),
            universe,
            _Parameters(),
            max_age,
        )


def test_registered_paper_strategy_decision_requires_durable_binding() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    work = PaperStrategyDecisionJob(
        _Strategy(),
        _context(decision_time, universe),
        universe,
        _Parameters(),
        FRESHNESS_MAX_AGE,
        dataset_version_id=uuid4(),
    )

    with pytest.raises(
        ValueError,
        match="PAPER_REGISTERED_STRATEGY_REQUIRES_DURABLE_BINDING",
    ):
        PaperJobDefinition("paper-strategy-decision", work)


def test_wrapped_strategy_decision_cannot_bypass_durable_binding() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    strategy = _Strategy(())
    decision = PaperStrategyDecisionJob(
        strategy,
        _context(decision_time, universe),
        universe,
        _Parameters(),
        FRESHNESS_MAX_AGE,
        dataset_version_id=uuid4(),
    )

    class _ProductionRuntime(PaperRuntimeContext):
        def __init__(self):
            object.__setattr__(self, "cycle", PaperCycleContext(
                create_scheduled_job_run("paper-wrapped-decision", decision_time)
            ))
            object.__setattr__(self, "recorded", [])
            object.__setattr__(self, "no_trade", [])

        def record_signal(self, signal):
            self.recorded.append(signal)
            return True

        def record_no_trade(self, **outcome):
            self.no_trade.append(outcome)
            return True

    runtime = _ProductionRuntime()
    wrapped = lambda active_runtime: decision(active_runtime)
    definition = PaperJobDefinition("paper-wrapped-decision", wrapped)

    with pytest.raises(
        RuntimeError,
        match="PAPER_STRATEGY_DECISION_REQUIRES_DURABLE_BINDING",
    ):
        definition.work(runtime)

    assert strategy.calls == []
    assert runtime.recorded == []
    assert runtime.no_trade == []


def test_runtime_proxy_cannot_bypass_durable_binding() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    strategy = _Strategy(())
    decision = PaperStrategyDecisionJob(
        strategy,
        _context(decision_time, universe),
        universe,
        _Parameters(),
        FRESHNESS_MAX_AGE,
        dataset_version_id=uuid4(),
    )

    class _ProductionRuntime(PaperRuntimeContext):
        def __init__(self):
            object.__setattr__(self, "cycle", PaperCycleContext(
                create_scheduled_job_run("paper-proxied-decision", decision_time)
            ))
            object.__setattr__(self, "recorded", [])
            object.__setattr__(self, "no_trade", [])

        def record_signal(self, signal):
            self.recorded.append(signal)
            return True

        def record_no_trade(self, **outcome):
            self.no_trade.append(outcome)
            return True

    class _RuntimeProxy:
        def __init__(self, runtime):
            self._runtime = runtime

        def __getattr__(self, name):
            return getattr(self._runtime, name)

    runtime = _ProductionRuntime()
    definition = PaperJobDefinition(
        "paper-proxied-decision",
        lambda active_runtime: decision(_RuntimeProxy(active_runtime)),
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_STRATEGY_DECISION_REQUIRES_DURABLE_BINDING",
    ):
        definition.work(runtime)

    assert strategy.calls == []
    assert runtime.recorded == []
    assert runtime.no_trade == []


@pytest.mark.parametrize(
    ("context_factory", "error_code"),
    (
        (
            lambda decision_time, universe: _context(
                decision_time,
                universe,
                age=FRESHNESS_MAX_AGE + timedelta(microseconds=1),
            ),
            "PAPER_MARKET_DATA_STALE",
        ),
        (
            lambda decision_time, universe: PITMarketContext(
                as_of=decision_time,
                bars=(),
            ),
            "PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT",
        ),
    ),
)
def test_paper_strategy_decision_job_rejects_unfresh_context_before_strategy_runs(
    context_factory,
    error_code: str,
) -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = context_factory(decision_time, universe)
    strategy = _Strategy(())
    runtime = _runtime(decision_time)

    with pytest.raises(RuntimeError, match=error_code):
        _durably_bound_decision(
            strategy,
            context,
            universe,
            _Parameters(),
            FRESHNESS_MAX_AGE,
        )(runtime)

    assert strategy.calls == []
    assert runtime.recorded == []
    assert runtime.no_trade == []


def test_paper_strategy_decision_job_records_valid_strategy_signals() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
        market_data_max_age=FRESHNESS_MAX_AGE,
    )
    signal = _signal(
        decision_time,
        instrument_id=universe.members[0].instrument_id,
        inputs_hash=inputs_hash,
    )
    strategy.signals = (signal,)
    runtime = _runtime(decision_time)

    _durably_bound_decision(
        strategy, context, universe, parameters, FRESHNESS_MAX_AGE
    )(runtime)

    assert strategy.calls == [
        (context, universe.version, universe.members, parameters, inputs_hash)
    ]
    assert runtime.recorded == [signal]


def test_paper_strategy_decision_job_allows_explicit_no_trade() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    strategy = _Strategy(())
    runtime = _runtime(decision_time)

    parameters = _Parameters()
    universe = _snapshot()
    context = _context(decision_time, universe)
    _durably_bound_decision(
        strategy, context, universe, parameters, FRESHNESS_MAX_AGE
    )(runtime)

    assert runtime.recorded == []
    assert runtime.no_trade == [{
        "strategy_version": strategy.version,
        "decision_time": decision_time,
        "inputs_hash": paper_decision_inputs_hash(
            strategy,
            context,
            universe,
            parameters,
            dataset_version_id=BOUND_DATASET_VERSION_ID,
            market_data_max_age=FRESHNESS_MAX_AGE,
        ),
    }]


def test_paper_strategy_decision_job_rejects_context_after_schedule_before_strategy_runs() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(scheduled_for + timedelta(seconds=1), universe)
    strategy = _Strategy(())
    runtime = _runtime(scheduled_for)

    with pytest.raises(ValueError, match="PAPER_DECISION_AFTER_JOB_SCHEDULE"):
        _durably_bound_decision(
            strategy, context, universe, _Parameters(), FRESHNESS_MAX_AGE
        )(runtime)

    assert strategy.calls == []
    assert runtime.recorded == []


def test_paper_strategy_decision_job_validates_all_outputs_before_persisting() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
        market_data_max_age=FRESHNESS_MAX_AGE,
    )
    valid = _signal(
        decision_time,
        instrument_id=universe.members[0].instrument_id,
        inputs_hash=inputs_hash,
    )
    invalid = _signal(
        decision_time + timedelta(seconds=1),
        instrument_id=universe.members[0].instrument_id,
        inputs_hash=inputs_hash,
    )
    strategy.signals = (valid, invalid)
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_DECISION_TIME_MISMATCH"):
        _durably_bound_decision(
            strategy, context, universe, parameters, FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_duplicate_signal_ids_before_persisting(
) -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
        market_data_max_age=FRESHNESS_MAX_AGE,
    )
    signal = _signal(
        decision_time,
        instrument_id=universe.members[0].instrument_id,
        inputs_hash=inputs_hash,
    )
    strategy.signals = (signal, signal)
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_DUPLICATE_SIGNAL_ID"):
        _durably_bound_decision(
            strategy, context, universe, parameters, FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []
    assert runtime.no_trade == []


def test_paper_strategy_decision_job_rejects_non_tuple_output() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    strategy = _Strategy([])
    runtime = _runtime(decision_time)

    with pytest.raises(TypeError, match="PAPER_STRATEGY_SIGNALS_MUST_BE_TUPLE"):
        _durably_bound_decision(
            strategy, context, universe, _Parameters(), FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_strategy_version_mismatch() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
        market_data_max_age=FRESHNESS_MAX_AGE,
    )
    strategy.signals = (
        _signal(
            decision_time,
            instrument_id=universe.members[0].instrument_id,
            version="wrong-version",
            inputs_hash=inputs_hash,
        ),
    )
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_VERSION_MISMATCH"):
        _durably_bound_decision(
            strategy, context, universe, parameters, FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_inputs_hash_mismatch_without_persisting() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    parameters = _TunedParameters(lookback=20)
    strategy = _Strategy(
        (
            _signal(
                decision_time,
                instrument_id=universe.members[0].instrument_id,
                inputs_hash="f" * 64,
            ),
        )
    )
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_INPUTS_HASH_MISMATCH"):
        _durably_bound_decision(
            strategy, context, universe, parameters, FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []


def test_paper_strategy_decision_job_rejects_signal_outside_universe() -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    universe = _snapshot()
    context = _context(decision_time, universe)
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
        market_data_max_age=FRESHNESS_MAX_AGE,
    )
    strategy.signals = (_signal(decision_time, inputs_hash=inputs_hash),)
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="PAPER_STRATEGY_SIGNAL_OUTSIDE_UNIVERSE"):
        _durably_bound_decision(
            strategy, context, universe, parameters, FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []


@pytest.mark.parametrize("interval_state", ["not_started", "expired"])
def test_paper_strategy_decision_job_rejects_signal_outside_membership_interval(
    interval_state: str,
) -> None:
    decision_time = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    instrument_id = uuid4()
    member = UniverseMember(
        instrument_id=instrument_id,
        valid_from=(
            decision_time + timedelta(seconds=1)
            if interval_state == "not_started"
            else None
        ),
        valid_to=decision_time if interval_state == "expired" else None,
    )
    universe = UniverseSnapshot(uuid4(), _version(), (member,))
    context = PITMarketContext(as_of=decision_time, bars=())
    parameters = _Parameters()
    strategy = _Strategy()
    inputs_hash = paper_decision_inputs_hash(
        strategy,
        context,
        universe,
        parameters,
        dataset_version_id=BOUND_DATASET_VERSION_ID,
        market_data_max_age=FRESHNESS_MAX_AGE,
    )
    strategy.signals = (
        _signal(
            decision_time,
            instrument_id=instrument_id,
            inputs_hash=inputs_hash,
        ),
    )
    runtime = _runtime(decision_time)

    with pytest.raises(ValueError, match="OUTSIDE_ACTIVE_UNIVERSE_MEMBERSHIP"):
        _durably_bound_decision(
            strategy, context, universe, parameters, FRESHNESS_MAX_AGE
        )(runtime)

    assert runtime.recorded == []
