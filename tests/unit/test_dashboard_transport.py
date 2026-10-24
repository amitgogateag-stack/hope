import pytest

from hope.api.dashboard_transport import validate_live_transport


def observation(**change):
    return {
        "schema_version": 1,
        "active_clients": 1,
        "max_concurrent_clients": 32,
        "rejected_clients_total": 0,
        "expired_clients_total": 0,
        **change,
    }


def test_live_transport_returns_only_fixed_safe_aggregate():
    assert validate_live_transport(observation(
        active_clients=2, rejected_clients_total=3, expired_clients_total=4,
    )) == {
        "active_clients": 2,
        "max_concurrent_clients": 32,
        "rejected_clients_total": 3,
        "expired_clients_total": 4,
    }


@pytest.mark.parametrize("value", [None, [], "private", 1, True])
def test_live_transport_rejects_non_object_payloads(value):
    with pytest.raises(ValueError, match="transport schema"):
        validate_live_transport(value)


@pytest.mark.parametrize("change", [
    {"schema_version": True},
    {"schema_version": 2},
    {"active_clients": 0},
    {"active_clients": True},
    {"active_clients": 33},
    {"max_concurrent_clients": 0},
    {"max_concurrent_clients": 257},
    {"rejected_clients_total": -1},
    {"rejected_clients_total": 1.5},
    {"expired_clients_total": True},
])
def test_live_transport_rejects_impossible_counts(change):
    with pytest.raises(ValueError, match="transport"):
        validate_live_transport(observation(**change))


def test_live_transport_rejects_missing_or_private_fields():
    missing = observation()
    del missing["expired_clients_total"]
    with pytest.raises(ValueError, match="transport schema"):
        validate_live_transport(missing)
    with pytest.raises(ValueError, match="transport schema"):
        validate_live_transport(observation(private_detail="secret"))
