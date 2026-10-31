"""
Guardrail Engine tests — minimum 15 pytest tests covering every edge case.
Run with: pytest tests/test_guardrails.py -v
"""

from datetime import datetime, timedelta, timezone
import pytest
from app.core.config import settings
from app.services.guardrails import GuardrailEngine, PortfolioState


def make_portfolio(**kwargs) -> PortfolioState:
    defaults = dict(
        current_value=25000.0,
        starting_capital=25000.0,
        daily_pnl=0.0,
        weekly_pnl=0.0,
        monthly_pnl=0.0,
        consecutive_losses=0,
        trades_today=0,
        cooling_off_until=None,
        peak_value=0.0,  # untracked by default — matches PortfolioState's own
                         # default so unrelated tests lowering current_value
                         # don't unintentionally trip the drawdown check too.
    )
    defaults.update(kwargs)
    return PortfolioState(**defaults)


@pytest.fixture
def engine():
    return GuardrailEngine()


# ── Normal conditions ─────────────────────────────────────────────────────────

def test_clean_portfolio_trading_allowed(engine):
    status = engine.check_all(make_portfolio())
    assert status.trading_allowed is True
    assert status.trading_mode == "normal"


def test_small_daily_loss_allowed(engine):
    status = engine.check_all(make_portfolio(daily_pnl=-100))
    assert status.trading_allowed is True


# ── Daily loss limit ──────────────────────────────────────────────────────────

def test_daily_loss_limit_exact_triggers(engine):
    """At exactly -2% daily loss, trading must stop."""
    pnl = -25000 * 0.02
    status = engine.check_all(make_portfolio(daily_pnl=pnl))
    assert status.trading_allowed is False
    assert "daily_loss_limit" in status.flags


def test_daily_loss_limit_over_triggers(engine):
    pnl = -25000 * 0.025
    status = engine.check_all(make_portfolio(daily_pnl=pnl))
    assert status.trading_allowed is False
    assert status.suspended_until is not None


def test_daily_loss_suspends_for_cooling_off_hours(engine):
    pnl = -25000 * 0.021
    status = engine.check_all(make_portfolio(daily_pnl=pnl))
    assert status.trading_allowed is False
    now = datetime.now(timezone.utc)
    assert status.suspended_until > now + timedelta(hours=20)


# ── Weekly loss limit ─────────────────────────────────────────────────────────

def test_weekly_loss_limit_triggers(engine):
    pnl = -25000 * 0.05
    status = engine.check_all(make_portfolio(weekly_pnl=pnl))
    assert status.trading_allowed is False
    assert "weekly_loss_limit" in status.flags


def test_weekly_loss_suspends_3_days(engine):
    pnl = -25000 * 0.06
    status = engine.check_all(make_portfolio(weekly_pnl=pnl))
    assert status.trading_allowed is False
    now = datetime.now(timezone.utc)
    assert status.suspended_until > now + timedelta(days=2)


# ── Monthly loss limit ────────────────────────────────────────────────────────

def test_monthly_loss_limit_triggers(engine):
    pnl = -25000 * 0.10
    status = engine.check_all(make_portfolio(monthly_pnl=pnl))
    assert status.trading_allowed is False
    assert "monthly_loss_limit" in status.flags


def test_monthly_loss_suspends_30_days(engine):
    pnl = -25000 * 0.11
    status = engine.check_all(make_portfolio(monthly_pnl=pnl))
    assert status.trading_allowed is False
    now = datetime.now(timezone.utc)
    assert status.suspended_until > now + timedelta(days=25)


# ── Maximum drawdown limit ────────────────────────────────────────────────────

def test_drawdown_limit_exact_triggers(engine):
    """At exactly 15% peak-to-trough drawdown, trading must stop."""
    status = engine.check_all(make_portfolio(
        peak_value=25000.0, current_value=25000.0 * 0.85,
    ))
    assert status.trading_allowed is False
    assert "max_drawdown_limit" in status.flags


def test_drawdown_below_limit_allowed(engine):
    status = engine.check_all(make_portfolio(
        peak_value=25000.0, current_value=25000.0 * 0.86,
    ))
    assert status.trading_allowed is True


def test_drawdown_suspends_30_days(engine):
    status = engine.check_all(make_portfolio(
        peak_value=25000.0, current_value=25000.0 * 0.80,
    ))
    now = datetime.now(timezone.utc)
    assert status.suspended_until > now + timedelta(days=25)


def test_drawdown_uses_peak_not_starting_capital(engine):
    """A book that grew to 30000 then fell to 30000*0.85 (=25500) is still
    'profitable' vs. a 25000 starting capital — but must still breach the
    drawdown limit, since it fell 15% from its own peak. Proves peak
    tracking is separate from starting-capital-relative checks."""
    status = engine.check_all(make_portfolio(
        starting_capital=25000.0, peak_value=30000.0, current_value=30000.0 * 0.85,
    ))
    assert status.trading_allowed is False
    assert "max_drawdown_limit" in status.flags


def test_drawdown_takes_priority_over_consecutive_losses(engine):
    """Drawdown (step 5) is evaluated before consecutive losses (step 6)."""
    status = engine.check_all(make_portfolio(
        peak_value=25000.0, current_value=25000.0 * 0.80,
        consecutive_losses=5,
    ))
    assert status.trading_allowed is False
    assert "max_drawdown_limit" in status.flags
    assert "consecutive_loss_limit" not in status.flags


def test_zero_peak_value_treated_as_current_value(engine):
    """peak_value=0.0 (untracked) must never divide-by-zero or fabricate a
    drawdown — it's treated as equal to current_value, i.e. 0% drawdown."""
    status = engine.check_all(make_portfolio(peak_value=0.0, current_value=20000.0))
    assert status.drawdown_pct == 0.0
    assert status.trading_allowed is True


def test_paper_visibility_mode_relaxes_drawdown_limit(monkeypatch):
    monkeypatch.setattr(settings, "broker", "ibkr")
    monkeypatch.setattr(settings, "ibkr_trading_mode", "paper")
    monkeypatch.setattr(settings, "paper_trade_visibility_mode", True)
    monkeypatch.setattr(settings, "paper_visibility_max_drawdown_pct", 0.30)

    pv_engine = GuardrailEngine()
    assert pv_engine.max_drawdown_pct == 0.30
    status = pv_engine.check_all(make_portfolio(
        peak_value=25000.0, current_value=25000.0 * 0.80,  # 20% drawdown
    ))
    assert status.trading_allowed is True


# ── Consecutive losses ────────────────────────────────────────────────────────

def test_consecutive_losses_limit_triggers(engine):
    status = engine.check_all(make_portfolio(consecutive_losses=3))
    assert status.trading_allowed is False
    assert "consecutive_loss_limit" in status.flags


def test_two_consecutive_losses_allowed(engine):
    status = engine.check_all(make_portfolio(consecutive_losses=2))
    assert status.trading_allowed is True


def test_consecutive_losses_suspends_48h(engine):
    status = engine.check_all(make_portfolio(consecutive_losses=3))
    now = datetime.now(timezone.utc)
    assert status.suspended_until > now + timedelta(hours=44)


# ── Cooling off period ────────────────────────────────────────────────────────

def test_active_cooling_off_blocks_trading(engine):
    future = datetime.now(timezone.utc) + timedelta(hours=12)
    status = engine.check_all(make_portfolio(cooling_off_until=future))
    assert status.trading_allowed is False


def test_expired_cooling_off_allows_trading(engine):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    status = engine.check_all(make_portfolio(cooling_off_until=past))
    assert status.trading_allowed is True


# ── Daily trade cap ───────────────────────────────────────────────────────────

def test_trade_cap_blocks_at_limit(engine):
    # Use the engine's configured limit so the test doesn't hardcode the cap value
    limit = engine.max_trades_per_day
    status = engine.check_all(make_portfolio(trades_today=limit))
    assert status.trading_allowed is False
    assert "daily_trade_cap" in status.flags


def test_trade_cap_allows_below_limit(engine):
    limit = engine.max_trades_per_day
    status = engine.check_all(make_portfolio(trades_today=max(0, limit - 1)))
    assert status.trading_allowed is True


# ── Capital preservation mode ─────────────────────────────────────────────────

def test_capital_preservation_mode_activates_at_threshold(engine):
    status = engine.check_all(make_portfolio(current_value=25000 * 0.85))
    assert status.trading_allowed is True
    assert status.trading_mode == "capital_preservation"
    assert "capital_preservation_mode" in status.flags


def test_capital_preservation_above_threshold_is_normal(engine):
    status = engine.check_all(make_portfolio(current_value=25000 * 0.86))
    assert status.trading_mode == "normal"


def test_capital_preservation_blocks_iron_condor(engine):
    status = engine.check_all(make_portfolio(current_value=25000 * 0.80))
    assert not engine.is_strategy_allowed("iron_condor", status)
    assert not engine.is_strategy_allowed("bull_call_debit_spread", status)


def test_capital_preservation_allows_credit_spreads(engine):
    status = engine.check_all(make_portfolio(current_value=25000 * 0.80))
    assert engine.is_strategy_allowed("bull_put_spread", status)
    assert engine.is_strategy_allowed("bear_call_spread", status)


def test_capital_preservation_halves_position_size(engine):
    status = engine.check_all(make_portfolio(current_value=25000 * 0.80))
    assert engine.get_position_size_multiplier(status) == 0.50


def test_normal_mode_full_position_size(engine):
    status = engine.check_all(make_portfolio())
    assert engine.get_position_size_multiplier(status) == 1.0


def test_capital_preservation_raises_signal_threshold(engine):
    status = engine.check_all(make_portfolio(current_value=25000 * 0.80))
    threshold = engine.get_signal_threshold(status)
    from app.core.config import settings
    assert threshold == settings.signal_score_preservation_mode


# ── Priority ordering ─────────────────────────────────────────────────────────

def test_cooling_off_takes_priority_over_daily_loss(engine):
    """Cooling off should be evaluated before other limits."""
    future = datetime.now(timezone.utc) + timedelta(hours=6)
    status = engine.check_all(make_portfolio(
        cooling_off_until=future,
        daily_pnl=-25000 * 0.021,
    ))
    assert status.trading_allowed is False
    assert "cooling_off_active" in status.flags


def test_paper_visibility_mode_relaxes_trade_cap(monkeypatch):
    monkeypatch.setattr(settings, "broker", "ibkr")
    monkeypatch.setattr(settings, "ibkr_trading_mode", "paper")
    monkeypatch.setattr(settings, "paper_trade_visibility_mode", True)
    monkeypatch.setattr(settings, "paper_visibility_max_trades_per_day", 20)

    engine = GuardrailEngine()

    assert engine.max_trades_per_day == 20
    status = engine.check_all(make_portfolio(trades_today=6))
    assert status.trading_allowed is True


def test_paper_visibility_mode_lowers_signal_threshold(monkeypatch):
    monkeypatch.setattr(settings, "broker", "ibkr")
    monkeypatch.setattr(settings, "ibkr_trading_mode", "paper")
    monkeypatch.setattr(settings, "paper_trade_visibility_mode", True)
    monkeypatch.setattr(settings, "paper_visibility_signal_score_threshold", 0.35)

    engine = GuardrailEngine()
    status = engine.check_all(make_portfolio())

    assert engine.get_signal_threshold(status) == 0.35


# ── Capital base divergence ───────────────────────────────────────────────────
#
# The limits in this module are percentages of `starting_capital`, a configured
# constant. Keeping that base fixed is deliberate — it stops the limits
# loosening as the account grows — but it only means what TRADING_POLICY.md
# says while the constant resembles the account it guards.
#
# Production ran with STARTING_CAPITAL at its 25,000 default against an account
# of about 245,000. A -$2,927 day, -1.2% of the real book, was computed as
# -11.71% and tripped the 2% daily limit, suspending bull_put_spread across
# 101 of 102 tickers for a month. Every number was correctly derived from a
# base ten times too small, which is why nobody saw it.
#
# These pin the detector. They do NOT pin a behaviour change: the flag must
# never alter trading_allowed, or this turns a misconfiguration into an outage.

from app.services.guardrails import (  # noqa: E402
    CAPITAL_BASE_DIVERGENT_FLAG,
    capital_base_is_divergent,
)


def test_divergence_helper_flags_an_account_far_above_the_base():
    assert capital_base_is_divergent(25_000.0, 244_861.92) is True


def test_divergence_helper_flags_an_account_far_below_the_base():
    """Symmetric: a base set too HIGH makes every limit far too loose, which
    is the more dangerous direction — the desk would keep trading through a
    loss the policy meant to stop."""
    assert capital_base_is_divergent(250_000.0, 25_000.0) is True


def test_divergence_helper_accepts_an_ordinary_working_account():
    # Up 40% on the configured base: real growth, limits still meaningful.
    assert capital_base_is_divergent(25_000.0, 35_000.0) is False
    assert capital_base_is_divergent(25_000.0, 25_000.0) is False


def test_divergence_helper_is_silent_when_a_figure_is_missing():
    """A failed broker read leaves current_value at 0. That is an unknown,
    not a divergence, and must not be reported as one."""
    assert capital_base_is_divergent(25_000.0, 0.0) is False
    assert capital_base_is_divergent(0.0, 244_861.92) is False
    assert capital_base_is_divergent(-1.0, 100.0) is False
    assert capital_base_is_divergent(25_000.0, 100.0, factor=0) is False


def test_divergent_base_raises_the_flag(engine):
    status = engine.check_all(make_portfolio(
        starting_capital=25_000.0, current_value=244_861.92,
    ))
    assert CAPITAL_BASE_DIVERGENT_FLAG in status.flags


def test_divergent_base_does_not_block_trading(engine):
    """The whole point: it reports, it does not halt. A flag that suspended
    the desk would turn a wrong constant into an outage."""
    status = engine.check_all(make_portfolio(
        starting_capital=25_000.0, current_value=244_861.92,
    ))
    assert status.trading_allowed is True
    assert status.trading_mode == "normal"


def test_matching_base_raises_no_flag(engine):
    status = engine.check_all(make_portfolio(
        starting_capital=25_000.0, current_value=25_000.0,
    ))
    assert CAPITAL_BASE_DIVERGENT_FLAG not in status.flags


def test_the_flag_survives_the_cooling_off_early_return(engine):
    """That path builds its own flag list and returned before this existed,
    so it would have dropped the flag exactly when an operator is most
    likely to be reading the status."""
    status = engine.check_all(make_portfolio(
        starting_capital=25_000.0, current_value=244_861.92,
        cooling_off_until=datetime.now(timezone.utc) + timedelta(hours=4),
    ))
    assert status.trading_allowed is False
    assert "cooling_off_active" in status.flags
    assert CAPITAL_BASE_DIVERGENT_FLAG in status.flags
