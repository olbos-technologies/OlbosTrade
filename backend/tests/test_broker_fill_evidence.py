from datetime import datetime, timezone
from decimal import Decimal
from dataclasses import replace

from app.services.broker_fill_evidence import FillEvidence, validate_fill_evidence


def example():
    return FillEvidence(
        broker_account_id="paper-account", broker_execution_id="exec-1",
        contract_id="contract-99", underlying="SPY",
        expiration="2026-11-20", strike=Decimal("500"), option_type="put",
        signed_filled_quantity=-1, filled_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        order_id="order-1", organization_id="tenant-a",
    )


def assess(e):
    return validate_fill_evidence(
        e, expected_account_id="paper-account", expected_organization_id="tenant-a",
    )


def test_complete_fill_evidence_is_structurally_verified():
    assert assess(example()).verified


def test_order_without_execution_evidence_fails():
    assert not assess(None).verified
    assert "fill_evidence_missing" in assess(None).reasons


def test_cross_account_and_cross_tenant_fills_fail():
    for change in ({"broker_account_id": "other"}, {"organization_id": "tenant-b"}):
        assert "account_scope_mismatch" in assess(replace(example(), **change)).reasons


def test_pending_or_unknown_execution_id_is_not_evidence():
    assert not assess(replace(example(), broker_execution_id="")).verified


def test_zero_filled_quantity_cannot_prove_position():
    assert not assess(replace(example(), signed_filled_quantity=0)).verified


def test_naive_fill_timestamp_cannot_prove_fresh_record():
    assert not assess(replace(example(), filled_at=datetime(2026, 10, 21))).verified
