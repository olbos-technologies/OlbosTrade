"""R2-A unit tests: contract identity and signed quantities, no live broker."""
from datetime import date
from decimal import Decimal

import pytest

from app.services.contract_reconciliation import ContractLeg, compare_contract_legs


def leg(strike="500", expiration=date(2026, 11, 20), kind="put", qty=-1):
    return ContractLeg("SPY", expiration, Decimal(strike), kind, qty)


def test_identical_signed_contracts_are_clean():
    result = compare_contract_legs([leg()], [leg()])
    assert result.verified and result.clean


def test_different_expiration_is_not_clean_even_if_symbol_and_size_match():
    result = compare_contract_legs([leg()], [leg(expiration=date(2026, 12, 18))])
    assert result.verified and not result.clean


def test_different_strike_is_not_clean():
    assert not compare_contract_legs([leg()], [leg(strike="495")]).clean


def test_different_option_type_is_not_clean():
    assert not compare_contract_legs([leg()], [leg(kind="call")]).clean


def test_signed_position_direction_matters():
    assert not compare_contract_legs([leg(qty=-1)], [leg(qty=1)]).clean


def test_missing_database_legs_cannot_be_treated_as_empty():
    result = compare_contract_legs([], None)
    assert not result.verified and not result.clean
    assert "db_contract_legs_unavailable" in result.reasons


def test_broker_ghost_and_database_phantom_are_not_clean():
    assert not compare_contract_legs([leg()], []).clean
    assert not compare_contract_legs([], [leg()]).clean


def test_invalid_zero_quantity_leg_unverified():
    result = compare_contract_legs([leg(qty=0)], [])
    assert not result.verified and not result.clean


@pytest.mark.asyncio
async def test_scoped_contract_check_empty_book_is_verified():
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.position_reconciler import PositionReconciler

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    reconciler = PositionReconciler(broker, organization_id=None)
    with patch("app.services.position_reconciler.AsyncSessionLocal") as db:
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ))
        db.return_value.__aenter__ = AsyncMock(return_value=session)
        result = await reconciler.check_contracts()
    assert result.verified and result.clean


@pytest.mark.asyncio
async def test_scoped_contract_check_nonempty_legacy_spread_is_unverified():
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.position_reconciler import PositionReconciler

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    reconciler = PositionReconciler(broker, organization_id=None)
    with patch("app.services.position_reconciler.AsyncSessionLocal") as db:
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[MagicMock()])))
        ))
        db.return_value.__aenter__ = AsyncMock(return_value=session)
        result = await reconciler.check_contracts()
    assert not result.verified and not result.clean
