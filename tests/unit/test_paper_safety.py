from uuid import uuid4
from hope.domain.execution.models import Environment, Order, OrderSide

def test_v01_order_model_is_explicitly_environment_scoped():
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY, quantity=1, environment=Environment.PAPER)
    assert order.environment is Environment.PAPER
