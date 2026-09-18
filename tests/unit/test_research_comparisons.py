from hope.application.experiments.comparisons import research_comparison_fingerprint


def test_research_comparison_fingerprint_is_canonical_and_content_sensitive() -> None:
    left = {"metrics": {"net": "10", "trades": 5}, "notes": ["a", "b"]}
    reordered = {"notes": ["a", "b"], "metrics": {"trades": 5, "net": "10"}}
    changed = {"metrics": {"net": "11", "trades": 5}, "notes": ["a", "b"]}

    assert research_comparison_fingerprint(left) == research_comparison_fingerprint(reordered)
    assert research_comparison_fingerprint(left) != research_comparison_fingerprint(changed)
