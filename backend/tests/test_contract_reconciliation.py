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


@pytest.mark.asyncio
async def test_scoped_contract_check_broker_unavailable_is_not_clean():
    from unittest.mock import AsyncMock, MagicMock
    from app.services.position_reconciler import PositionReconciler

    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("offline"))
    result = await PositionReconciler(broker, organization_id=None).check_contracts()
    assert not result.verified and not result.clean


@pytest.mark.asyncio
async def test_scoped_contract_check_db_unavailable_is_not_clean():
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.position_reconciler import PositionReconciler

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    with patch("app.services.position_reconciler.AsyncSessionLocal", side_effect=RuntimeError("db unavailable")):
        result = await PositionReconciler(broker, organization_id=None).check_contracts()
    assert not result.verified and not result.clean


@pytest.mark.asyncio
async def test_scoped_contract_check_untracked_broker_contract_is_not_clean():
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.position_reconciler import PositionReconciler
    from app.broker.broker_interface import Position

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[Position(
        symbol="SPY option", underlying="SPY", expiration=date(2026, 11, 20),
        strike=Decimal("500"), option_type="put", quantity=-1,
        avg_cost=Decimal("1.50"),
    )])
    with patch("app.services.position_reconciler.AsyncSessionLocal") as db:
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ))
        db.return_value.__aenter__ = AsyncMock(return_value=session)
        result = await PositionReconciler(broker, organization_id=None).check_contracts()
    assert result.verified and not result.clean
    assert result.broker_only


@pytest.mark.parametrize("field,value", [
    ("quantity", 1.5), ("quantity", True), ("quantity", "1"),
    ("quantity", float("nan")), ("quantity", float("inf")),
    ("underlying", "   "), ("underlying", None),
    ("expiration", None), ("expiration", "2026-11-20"),
    ("strike", Decimal("NaN")), ("strike", Decimal("Infinity")),
    ("strike", Decimal("-1")), ("strike", Decimal("0")),
    ("strike", "bad"), ("option_type", None),
])
@pytest.mark.parametrize("source", ["broker", "database"])
def test_invalid_contract_evidence_is_unverified(field, value, source):
    from dataclasses import replace
    invalid = replace(leg(), **{field: value})
    broker, recorded = ([invalid], []) if source == "broker" else ([], [invalid])
    result = compare_contract_legs(broker, recorded)
    assert not result.verified and not result.clean
    assert result.reasons == ["invalid_contract_leg"]


def test_fractional_quantity_cannot_be_truncated_into_a_match():
    result = compare_contract_legs([leg(qty=-1.5)], [leg(qty=-1)])
    assert not result.verified and not result.clean


def test_valid_normalization_and_signed_aggregation_are_preserved():
    from dataclasses import replace
    result = compare_contract_legs(
        [replace(leg(qty=-1), underlying=" spy ", option_type=" PUT "), leg(qty=-1)],
        [leg(qty=-2)],
    )
    assert result.verified and result.clean
