"""No trading style may risk more per trade than the charter allows.

TRADING_POLICY.md sets max account risk per trade at 2%. Aggressive was found at
2.25% (2026-11), over the ceiling, with nothing to catch it. This pins every style
to the ceiling, and asserts it actually reads the styles so it cannot pass empty.
"""
from app.services.trading_mode import TRADING_MODES

CHARTER_MAX_RISK_PER_TRADE = 0.02


def test_every_style_is_within_the_charter_ceiling():
    assert len(TRADING_MODES) >= 4, "the check is not reading the real styles"
    for mode, cfg in TRADING_MODES.items():
        assert 0 < cfg.risk_per_trade_pct <= CHARTER_MAX_RISK_PER_TRADE, mode


def test_aggressive_is_exactly_the_ceiling():
    by_name = {m.value: c for m, c in TRADING_MODES.items()}
    assert any(abs(c.risk_per_trade_pct - 0.02) < 1e-12 for c in by_name.values())
