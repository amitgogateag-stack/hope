from uuid import uuid4

import pytest

from hope.domain.execution.models import Environment, Order, OrderSide


def make_order(environment: Environment) -> Order:
    return Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=uuid4(),
        side=OrderSide.BUY,
        quantity=1,
        environment=environment,
    )


def test_v01_order_model_is_explicitly_environment_scoped():
    order = make_order(Environment.PAPER)
    assert order.environment is Environment.PAPER


def test_paper_safety_accepts_only_paper_environment():
    make_order(Environment.PAPER).assert_paper_safe()


@pytest.mark.parametrize(
    "environment",
    [Environment.RESEARCH, Environment.BACKTEST, Environment.WALK_FORWARD],
)
def test_paper_safety_rejects_non_paper_environments(environment):
    with pytest.raises(ValueError, match="PAPER_EXECUTION_REQUIRES_PAPER_ENVIRONMENT"):
        make_order(environment).assert_paper_safe()
