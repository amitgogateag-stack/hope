from datetime import datetime, timezone

import pytest

from hope.domain.config import canonical_json, configuration_hash
from hope.domain.data import DataQualityError, MarketBar
from hope.domain.enums import Environment, IdentityStatus
from hope.domain.identity import IdentityMapping, IdentityResolutionError, resolve_evaluable_mappings
from hope.domain.invariants import InvariantViolation, check_frozen_universe_count, check_unique_canonical_ids, run_invariants
from hope.domain.safety import ExecutionCapability, SafetyViolation, paper_capability


def test_configuration_hash_is_order_independent_and_sensitive():
    a = {"z": 2, "a": {"beta": 1, "alpha": True}}
    b = {"a": {"alpha": True, "beta": 1}, "z": 2}
    assert canonical_json(a) == canonical_json(b)
    assert configuration_hash(a) == configuration_hash(b)
    assert configuration_hash({"z": 3, "a": a["a"]}) != configuration_hash(a)


def test_duplicate_active_broker_identity_is_rejected():
    mappings = [
        IdentityMapping("KALPATPOWR-EQ", "canonical-a", "KPIL-EQ", IdentityStatus.ACTIVE),
        IdentityMapping("KPIL-EQ", "canonical-b", "KPIL-EQ", IdentityStatus.ACTIVE),
    ]
    with pytest.raises(IdentityResolutionError):
        resolve_evaluable_mappings(mappings)


def test_terminal_identity_is_not_evaluable():
    mapping = IdentityMapping("OLD-EQ", "canonical-a", "OLD-EQ", IdentityStatus.TERMINAL)
    assert resolve_evaluable_mappings([mapping]) == ()


def test_future_bar_is_rejected():
    decision = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    bar = MarketBar("ABC", decision, datetime(2026, 8, 31, 14, 1, tzinfo=timezone.utc), 10, 11, 9, 10.5, 100)
    with pytest.raises(DataQualityError):
        bar.validate(decision)


def test_future_event_is_rejected_even_when_available():
    decision = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    future_event = datetime(2026, 8, 31, 14, 1, tzinfo=timezone.utc)
    bar = MarketBar("ABC", future_event, decision, 10, 11, 9, 10.5, 100)
    with pytest.raises(DataQualityError):
        bar.validate(decision)


def test_non_finite_market_data_is_rejected():
    ts = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    bar = MarketBar("ABC", ts, ts, float("nan"), 11, 9, 10.5, 100)
    with pytest.raises(DataQualityError):
        bar.validate(ts)


def test_impossible_ohlc_is_rejected():
    ts = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    bar = MarketBar("ABC", ts, ts, 10, 9, 8, 10.5, 100)
    with pytest.raises(DataQualityError):
        bar.validate(ts)


def test_paper_has_no_live_order_capability():
    capability = paper_capability()
    assert capability.environment is Environment.PAPER
    assert capability.live_order_submission is False


def test_live_environment_is_hard_disabled():
    with pytest.raises(SafetyViolation):
        ExecutionCapability(Environment.LIVE).assert_safe()


def test_live_order_submission_is_hard_disabled():
    with pytest.raises(SafetyViolation):
        ExecutionCapability(Environment.PAPER, live_order_submission=True).assert_safe()


def test_core_invariants_fail_closed():
    assert check_frozen_universe_count(256, 256).passed
    assert not check_frozen_universe_count(255, 256).passed
    assert check_unique_canonical_ids(["a", "b"]).passed
    assert not check_unique_canonical_ids(["a", "a"]).passed
    with pytest.raises(InvariantViolation, match="INVARIANT-001"):
        run_invariants([lambda: check_frozen_universe_count(1, 2)])
