from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from app.services.contract_reconciliation import ContractLeg
from app.services.ledger_reconciliation import compare_scoped_broker_to_ledger


def test_scoped_reconciliation_rejects_other_broker_account():
    record = SimpleNamespace(
        broker="ibkr", broker_account_id="account-B", organization_id=None,
        underlying="SPY", expiration="2026-11-20", strike=Decimal("500"),
        option_type="put", signed_filled_quantity=-1,
    )
    broker = [ContractLeg("SPY", date(2026,11,20), Decimal("500"), "put", -1)]
    result = compare_scoped_broker_to_ledger(
        broker, [record], broker="ibkr", broker_account_id="account-A",
        organization_id=None, coverage_verified=True,
    )
    assert not result.verified and not result.clean
    assert "execution_account_scope_mismatch" in result.reasons


def test_scoped_reconciliation_accepts_matched_account_only_with_coverage():
    record = SimpleNamespace(
        broker="ibkr", broker_account_id="account-A", organization_id=None,
        underlying="SPY", expiration="2026-11-20", strike=Decimal("500"),
        option_type="put", signed_filled_quantity=-1,
    )
    broker = [ContractLeg("SPY", date(2026,11,20), Decimal("500"), "put", -1)]
    result = compare_scoped_broker_to_ledger(
        broker, [record], broker="ibkr", broker_account_id="account-A",
        organization_id=None, coverage_verified=False,
    )
    assert not result.verified
