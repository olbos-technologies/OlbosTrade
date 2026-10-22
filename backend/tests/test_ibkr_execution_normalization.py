from datetime import datetime, timezone
from types import SimpleNamespace
from decimal import Decimal
import pytest
from app.services.ibkr_execution_normalization import normalize_ibkr_option_fill


def sample(**changes):
    execution=dict(acctNumber="DU123",side="SLD",shares=2,price=1.25,
                   execId="0001.01",orderId=123)
    execution.update({k:v for k,v in changes.items() if k != "time"})
    contract=SimpleNamespace(secType="OPT",right="P",symbol="SPY",
                             lastTradeDateOrContractMonth="20261120",
                             strike=500,conId=123456)
    return SimpleNamespace(execution=SimpleNamespace(**execution),contract=contract, time=changes.get("time", datetime(2026,10,22,tzinfo=timezone.utc)))


def test_short_option_execution_is_signed_negative():
    normalized=normalize_ibkr_option_fill(sample(),expected_account="DU123",organization_id=None)
    assert normalized.evidence.signed_filled_quantity == -2
    assert normalized.price == Decimal("1.25")


def test_other_broker_account_rejected():
    with pytest.raises(ValueError,match="account_scope_mismatch"):
        normalize_ibkr_option_fill(sample(acctNumber="DU999"),expected_account="DU123",organization_id=None)


def test_fractional_option_contracts_rejected():
    with pytest.raises(ValueError,match="invalid_filled_quantity"):
        normalize_ibkr_option_fill(sample(shares=1.5),expected_account="DU123",organization_id=None)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError,match="execution_timestamp_unverified"):
        normalize_ibkr_option_fill(sample(time=datetime(2026,10,22)),expected_account="DU123",organization_id=None)
