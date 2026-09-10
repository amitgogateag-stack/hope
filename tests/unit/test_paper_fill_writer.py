from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperEffectType, paper_fill_payload_hash
from hope.application.paper.fills import PaperFillWriter
from hope.domain.execution import Fill, OrderSide

UTC = timezone.utc


class FakePersistence:
    def __init__(self, result=True):
        self.result = result
        self.calls = []
    def persist(self, effect, fill):
        self.calls.append((effect, fill))
        return self.result


def make_context():
    return PaperCycleContext(create_scheduled_job_run("paper-fill-writer", datetime(2026, 9, 9, 23, 30, tzinfo=UTC)))


def make_fill(context, sequence=0):
    signal_id = uuid4()
    return Fill(context.fill_id(signal_id, sequence), context.order_id(signal_id), signal_id, uuid4(), OrderSide.BUY,
                Decimal("2"), Decimal("101.5"), Decimal("0.25"), Decimal("0.5"), "cost-v1",
                datetime(2026, 9, 9, 23, 29, tzinfo=UTC))


def test_writer_records_fill_effect_with_job_lineage():
    context = make_context(); fill = make_fill(context); repo = FakePersistence()
    assert PaperFillWriter(repo).record(context, fill, sequence=0) is True
    effect, persisted = repo.calls[0]
    assert persisted == fill
    assert effect.effect_type is PaperEffectType.FILL
    assert effect.entity_id == fill.fill_id
    assert effect.job_run_id == context.job_run.job_run_id
    assert effect.payload_hash == paper_fill_payload_hash(fill)


def test_writer_preserves_duplicate_result():
    context = make_context(); fill = make_fill(context); repo = FakePersistence(False)
    assert PaperFillWriter(repo).record(context, fill, sequence=0) is False


def test_writer_rejects_wrong_sequence_identity():
    context = make_context(); fill = make_fill(context, 0); repo = FakePersistence()
    with pytest.raises(ValueError, match="PAPER_FILL_IDENTITY_MISMATCH"):
        PaperFillWriter(repo).record(context, fill, sequence=1)
    assert repo.calls == []


@pytest.mark.parametrize("field,value,code", [
    ("quantity", Decimal("NaN"), "PAPER_FILL_QUANTITY_INVALID"),
    ("price", Decimal("Infinity"), "PAPER_FILL_PRICE_INVALID"),
    ("commission", Decimal("-1"), "PAPER_FILL_COMMISSION_INVALID"),
    ("slippage", Decimal("-1"), "PAPER_FILL_SLIPPAGE_INVALID"),
])
def test_payload_hash_rejects_invalid_numeric_content(field, value, code):
    context = make_context(); fill = make_fill(context)
    with pytest.raises(ValueError, match=code):
        paper_fill_payload_hash(fill.__class__(**{**fill.__dict__, field: value}))
