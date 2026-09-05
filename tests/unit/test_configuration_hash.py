from hope.application.experiments.config_hash import canonical_json, configuration_hash

def test_hash_is_deterministic_and_order_independent():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert canonical_json(a) == canonical_json(b)
    assert configuration_hash(a) == configuration_hash(b)

def test_hash_changes_when_configuration_changes():
    assert configuration_hash({"gapPct": 2.0}) != configuration_hash({"gapPct": 2.1})
