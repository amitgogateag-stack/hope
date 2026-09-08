from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hope.domain.research import CounterfactualAcceptancePolicy, CounterfactualExitAssumption


def make_policy(**overrides) -> CounterfactualAcceptancePolicy:
    values = {
        "policy_id": "risk-override-diagnostic",
        "version": "1",
        "quantity": Decimal("1"),
        "holding_period": timedelta(days=5),
    }
    values.update(overrides)
    return CounterfactualAcceptancePolicy(**values)


def test_counterfactual_policy_is_explicit_and_immutable():
    policy = make_policy()

    assert policy.quantity == Decimal("1")
    assert policy.holding_period == timedelta(days=5)
    assert policy.execution_assumption == "SAME_AS_PRIMARY"
    assert policy.exit_assumption is CounterfactualExitAssumption.MARK_TO_HORIZON

    with pytest.raises(ValidationError):
        policy.quantity = Decimal("2")


def test_counterfactual_policy_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        make_policy(quantity=Decimal("0"))


def test_counterfactual_policy_rejects_non_positive_holding_period():
    with pytest.raises(ValidationError, match="COUNTERFACTUAL_HOLDING_PERIOD_MUST_BE_POSITIVE"):
        make_policy(holding_period=timedelta(0))


def test_counterfactual_policy_rejects_blank_identity_and_unknown_fields():
    with pytest.raises(ValidationError, match="COUNTERFACTUAL_POLICY_IDENTIFIER_REQUIRED"):
        make_policy(policy_id="   ")

    with pytest.raises(ValidationError):
        make_policy(hidden_execution_shortcut=True)


def test_counterfactual_policy_forbids_alternate_execution_assumptions():
    with pytest.raises(ValidationError):
        make_policy(execution_assumption="FRICTIONLESS")
