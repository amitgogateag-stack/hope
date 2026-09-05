from hope.domain.validation.invariants import INITIAL_INVARIANTS

def test_initial_invariant_registry_has_ten_required_invariants():
    ids = [x.invariant_id for x in INITIAL_INVARIANTS]
    assert ids == [f"INVARIANT-{i:03d}" for i in range(1, 11)]
