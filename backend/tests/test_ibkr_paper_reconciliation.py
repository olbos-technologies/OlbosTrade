from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from app.services.contract_reconciliation import ContractLeg
from app.services.ibkr_paper_reconciliation import compare_ibkr_paper_snapshot


def session(accounts=("DU123",),connected=True):
    return SimpleNamespace(isConnected=lambda:connected,managedAccounts=lambda:list(accounts))


def test_equal_positions_cannot_establish_complete_execution_history():
    broker=[ContractLeg("SPY",date(2026,11,20),Decimal("500"),"put",-1)]
    rows=[SimpleNamespace(broker="ibkr",broker_account_id="DU123",
        organization_id=None,underlying="SPY",expiration="2026-11-20",
        strike=Decimal("500"),option_type="put",signed_filled_quantity=-1)]
    result=compare_ibkr_paper_snapshot(session(),broker,rows,
        expected_account_id="DU123",organization_id=None)
    assert not result.verified and not result.clean
    assert "execution_coverage_unverified" in result.reasons


def test_disconnected_session_cannot_report_clean():
    result=compare_ibkr_paper_snapshot(session(connected=False),[],[],
        expected_account_id="DU123",organization_id=None)
    assert not result.verified and "broker_session_disconnected" in result.reasons


def test_multi_account_session_is_rejected():
    result=compare_ibkr_paper_snapshot(session(("DU123","DU456")),[],[],
        expected_account_id="DU123",organization_id=None)
    assert not result.verified and "broker_session_account_mismatch" in result.reasons


def test_live_account_is_rejected():
    result=compare_ibkr_paper_snapshot(session(),[],[],
        expected_account_id="U123",organization_id=None)
    assert not result.verified and "ibkr_paper_account_required" in result.reasons
