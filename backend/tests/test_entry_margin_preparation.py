"""Entry margin preparation is independent of broker authorization."""
from types import SimpleNamespace

from app.services.entry_margin_preparation import evaluate_entry_margin_snapshot
from app.services.margin_monitor import evaluate_margin


def account(**overrides):
    fields = dict(is_stale=False, data_age_seconds=0,
                  net_liquidation=10000, maintenance_margin=9000,
                  excess_liquidity=1000, buying_power=1500, init_margin=9000)
    fields.update(overrides)
    return SimpleNamespace(**fields)


def decision(snapshot):
    return evaluate_entry_margin_snapshot(
        snapshot, evaluate_margin=evaluate_margin,
        warn_pct=0.5, critical_pct=0.8,
    )


def test_fresh_critical_margin_blocks():
    reason, stale_age = decision(account())
    assert reason is not None and reason.startswith("margin_critical:")
    assert stale_age is None


def test_stale_critical_snapshot_cannot_establish_a_margin_decision():
    reason, stale_age = decision(account(is_stale=True, data_age_seconds=90))
    assert reason is None
    assert stale_age == 90


def test_missing_maintenance_margin_is_not_affirmative_clearance():
    reason, stale_age = decision(account(maintenance_margin=None))
    assert reason is None
    assert stale_age is None


def test_fresh_healthy_margin_does_not_trigger_critical_block():
    reason, stale_age = decision(account(maintenance_margin=1000, excess_liquidity=9000))
    assert reason is None
    assert stale_age is None
