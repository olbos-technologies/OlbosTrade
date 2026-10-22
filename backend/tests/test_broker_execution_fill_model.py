"""Ledger schema invariants; no order submission or database writes."""
from app.models.broker_execution_fill import BrokerExecutionFill


def test_broker_execution_identity_is_unique_per_account():
    constraints = list(BrokerExecutionFill.__table__.constraints)
    unique = [c for c in constraints if c.name == "uq_broker_execution_fills_identity"]
    assert len(unique) == 1
    assert {column.name for column in unique[0].columns} == {
        "broker", "broker_account_id", "broker_execution_id"
    }


def test_fill_identity_and_signed_quantity_are_required():
    columns = BrokerExecutionFill.__table__.columns
    for name in ("broker", "broker_account_id", "broker_execution_id",
                 "contract_id", "signed_filled_quantity", "filled_at",
                 "fill_price"):
        assert not columns[name].nullable


def test_tenant_owner_is_explicit_not_inferred():
    assert BrokerExecutionFill.__table__.columns["organization_id"].nullable
