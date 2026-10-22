"""Regression checks for immutable replay comparison."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace


def test_fill_ledger_contract_distinguishes_quantity_and_price():
    from app.models.broker_execution_fill import BrokerExecutionFill
    from sqlalchemy import UniqueConstraint
    table = BrokerExecutionFill.__table__
    assert any(isinstance(c, UniqueConstraint) and c.name == "uq_broker_execution_fills_identity"
               for c in table.constraints)
    assert table.columns["signed_filled_quantity"].nullable is False
    assert table.columns["fill_price"].nullable is False
