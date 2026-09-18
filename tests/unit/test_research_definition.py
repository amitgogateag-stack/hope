import pytest

from hope.domain.research.models import ExperimentDefinition, ExperimentVariantDefinition


BASE = {
    "experiment_id": "EXP-1",
    "hypothesis": "Momentum persists after cost",
    "strategy_version": "STRATEGY-1",
    "dataset_version": "DATASET-1",
    "universe_version": "UNIVERSE-1",
    "configuration_hash": "a" * 64,
}


@pytest.mark.parametrize(
    "field",
    [
        "experiment_id",
        "strategy_version",
        "dataset_version",
        "universe_version",
    ],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_research_identity_fields_must_be_nonblank(field, value) -> None:
    with pytest.raises(ValueError, match="RESEARCH_IDENTITY_REQUIRED"):
        ExperimentDefinition(**(BASE | {field: value}))


@pytest.mark.parametrize(
    "field",
    [
        "experiment_id",
        "strategy_version",
        "dataset_version",
        "universe_version",
    ],
)
@pytest.mark.parametrize("value", [" padded", "padded "])
def test_research_identity_fields_must_be_canonical(field, value) -> None:
    with pytest.raises(ValueError, match="RESEARCH_IDENTITY_NOT_CANONICAL"):
        ExperimentDefinition(**(BASE | {field: value}))


@pytest.mark.parametrize("value", ["", "   "])
def test_research_hypothesis_must_be_nonblank(value) -> None:
    with pytest.raises(ValueError, match="RESEARCH_HYPOTHESIS_REQUIRED"):
        ExperimentDefinition(**(BASE | {"hypothesis": value}))


@pytest.mark.parametrize("value", [" hypothesis", "hypothesis "])
def test_research_hypothesis_must_be_canonical(value) -> None:
    with pytest.raises(ValueError, match="RESEARCH_HYPOTHESIS_NOT_CANONICAL"):
        ExperimentDefinition(**(BASE | {"hypothesis": value}))


def test_research_definition_accepts_canonical_provenance() -> None:
    assert ExperimentDefinition(**BASE).experiment_id == "EXP-1"



def test_experiment_variant_definition_requires_distinct_control_and_variant() -> None:
    with pytest.raises(ValueError, match="CONTROL_MUST_DIFFER"):
        ExperimentVariantDefinition(
            control_experiment_id="EXP-1",
            variant_experiment_id="EXP-1",
            variant_label="lookback-variant",
        )


@pytest.mark.parametrize("field", ["control_experiment_id", "variant_experiment_id"])
@pytest.mark.parametrize("value", ["", "   ", " padded", "padded "])
def test_experiment_variant_definition_requires_canonical_ids(field, value) -> None:
    kwargs = {
        "control_experiment_id": "EXP-CONTROL",
        "variant_experiment_id": "EXP-VARIANT",
        "variant_label": "lookback-variant",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="RESEARCH_VARIANT_EXPERIMENT_ID"):
        ExperimentVariantDefinition(**kwargs)


@pytest.mark.parametrize("value", ["", "   ", " padded", "padded "])
def test_experiment_variant_definition_requires_canonical_label(value) -> None:
    with pytest.raises(ValueError, match="RESEARCH_VARIANT_LABEL"):
        ExperimentVariantDefinition(
            control_experiment_id="EXP-CONTROL",
            variant_experiment_id="EXP-VARIANT",
            variant_label=value,
        )
