"""Advisory evaluate-equity — no order placement."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.api.routes.trade_desk import evaluate_equity, EquityEvaluateRequest
from app.services.execution_mode import execution_mode_manager, ExecutionMode
from app.services.execution_portfolio_gate import PortfolioGateResult


@pytest.mark.asyncio
async def test_evaluate_equity_blocks_on_kill_switch(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.trade_desk._is_kill_switch_active", lambda: True
    )
    monkeypatch.setattr(
        "app.core.config.settings.market_hours_only", False
    )
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new_callable=AsyncMock) as fetch:
        fetch.side_effect = Exception("skip")
        with patch("app.broker.broker_factory.get_broker", side_effect=Exception("no broker")):
            body = await evaluate_equity(
                EquityEvaluateRequest(
                    ticker="AAPL",
                    action="BUY",
                    shares=10,
                    entry_price=100,
                    stop_price=98,
                    target_price=104,
                )
            )
    assert body["final_status"] == "BLOCKED"
    assert any("Kill switch" in r for r in body["block_reasons"])


@pytest.mark.asyncio
async def test_evaluate_equity_manual_eligible_when_clear(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.trade_desk._is_kill_switch_active", lambda: False
    )
    monkeypatch.setattr(
        "app.core.config.settings.market_hours_only", False
    )
    monkeypatch.setattr(
        "app.core.config.settings.ibkr_trading_mode", "paper"
    )
    execution_mode_manager._mode = ExecutionMode.MANUAL

    # current_value must be a real number: MagicMock()'s default __float__
    # returns 1.0, which silently turned this into a $1 fake portfolio where
    # any nonzero trade risk blows through the 25% concentration cap and the
    # test failed with final_status="BLOCKED" instead of exercising the
    # intended clean-portfolio path.
    portfolio = MagicMock()
    portfolio.current_value = 100_000.0
    guard = MagicMock()
    guard.trading_allowed = True
    guard.trading_mode = "normal"
    guard.reason = None

    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new_callable=AsyncMock, return_value=portfolio):
        with patch("app.api.routes.trade_desk.GuardrailEngine") as GE:
            GE.return_value.check_all.return_value = guard
            with patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
                with patch(
                    "app.services.account_guard.verify_account_mode",
                    new_callable=AsyncMock,
                    return_value=(True, "ok"),
                ):
                    # Regime import inside evaluate uses app.main._current_regime
                    with patch("app.main._current_regime", None), \
                         patch("app.services.execution_portfolio_gate.check_execution_portfolio", new=AsyncMock(return_value=PortfolioGateResult(allowed=True, reason=None))):
                        body = await evaluate_equity(
                            EquityEvaluateRequest(
                                ticker="AAPL",
                                action="BUY",
                                shares=5,
                                entry_price=100,
                                stop_price=97,
                                target_price=106,
                            )
                        )
    assert body["final_status"] == "MANUAL_ELIGIBLE"
    assert body["environment"] == "paper"
    assert body["estimated_max_loss"] == 15.0
    assert body["block_reasons"] == []
