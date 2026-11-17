"""Aggregate lineage must not label impossible accounting as complete."""
from datetime import datetime, timezone

import pytest

from hope.api.dashboard_projection import SECTIONS, verified_projections


def observed(**changes):
    value = {name: {"status": "UNAVAILABLE"} for name in SECTIONS}
    value["execution_ledger"] = dict(status="AVAILABLE", fills=2, applied_fills=2,
        fill_applications=2, accounting_receipts=2, fills_without_application=0,
        applications_without_pnl=0, effect_receipt_fills=2,
        fills_without_effect_receipt=0, effect_receipt_fills_without_application=0,
        accounting_receipts_without_application=0, unattributed_effect_receipt_fills=0)
    value["execution_ledger"].update(changes)
    # This corruption fixture explicitly models SUCCEEDED receipt owners.
    ledger = value["execution_ledger"]
    for state in ('claimed', 'succeeded', 'failed'):
        ledger[state+'_effect_receipt_fills'] = 0
        ledger[state+'_effect_receipt_fills_without_application'] = 0
    ledger['succeeded_effect_receipt_fills'] = ledger['effect_receipt_fills']
    ledger['succeeded_effect_receipt_fills_without_application'] = ledger['effect_receipt_fills_without_application']
    return value


@pytest.mark.parametrize("changes", [
    dict(applied_fills=0, fill_applications=0, accounting_receipts=0,
         fills_without_application=2),
    dict(applied_fills=1, fill_applications=1, accounting_receipts=1,
         fills_without_application=1),
    dict(accounting_receipts=0),
    dict(accounting_receipts=3),
    dict(accounting_receipts_without_application=1),
    dict(accounting_receipts=1, applications_without_pnl=0),
])
def test_impossible_receipt_intersections_are_refused(changes):
    with pytest.raises(ValueError, match="LEDGER_COUNT_INCONSISTENT"):
        verified_projections(observed(**changes), datetime.now(timezone.utc))


@pytest.mark.parametrize("changes,state", [
    ({}, "OBSERVED_COMPLETE"),
    (dict(applied_fills=0, fill_applications=0, accounting_receipts=0,
          fills_without_application=2, effect_receipt_fills_without_application=2),
     "SUCCEEDED_EFFECT_RECEIPTS_NOT_APPLIED"),
    (dict(accounting_receipts=1, applications_without_pnl=1),
     "APPLICATIONS_WITHOUT_ACCOUNTING_RECEIPTS"),
    (dict(accounting_receipts=3, accounting_receipts_without_application=1),
     "ORPHAN_ACCOUNTING_RECEIPTS"),
    (dict(effect_receipt_fills=1, fills_without_effect_receipt=1),
     "MISSING_EFFECT_RECEIPTS"),
])
def test_consistent_partial_and_complete_lineage_remain_distinct(changes, state):
    result = verified_projections(observed(**changes), datetime.now(timezone.utc))
    assert result["execution_ledger"]["reconciliation_state"] == state
