from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.contract_reconciliation import ContractLeg
from app.services.ledger_reconciliation import (
    compare_broker_to_ledger, legs_from_executions,
)


def fill(quantity, strike="500", kind="put"):
    return SimpleNamespace(underlying="SPY", expiration="2026-11-20",
                           strike=Decimal(str(strike)), option_type=kind,
                           signed_filled_quantity=quantity)


def test_partial_fills_combine_signed_contract_quantities():
    legs = legs_from_executions([fill(-1), fill(-2)])
    assert len(legs) == 1 and legs[0].quantity == -3


def test_closing_fill_nets_to_zero():
    assert legs_from_executions([fill(-2), fill(2)]) == []


def test_unknown_history_must_never_authorize_clean_portfolio():
    broker = [ContractLeg("SPY", date(2026, 11, 20), Decimal("500"), "put", -1)]
    result = compare_broker_to_ledger(broker, [fill(-1)], coverage_verified=False)
    assert not result.verified and not result.clean


def test_known_complete_matching_history_is_clean():
    broker = [ContractLeg("SPY", date(2026, 11, 20), Decimal("500"), "put", -2)]
    result = compare_broker_to_ledger(broker, [fill(-1), fill(-1)], coverage_verified=True)
    assert result.verified and result.clean


def test_broker_position_mismatch_exposed():
    broker = [ContractLeg("SPY", date(2026, 11, 20), Decimal("500"), "put", -3)]
    result = compare_broker_to_ledger(broker, [fill(-2)], coverage_verified=True)
    assert result.verified and not result.clean


def test_malformed_fill_fails_closed():
    assert legs_from_executions([fill(0)]) is None
    assert legs_from_executions([fill(-1, "NaN")]) is None
