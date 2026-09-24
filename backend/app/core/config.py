"""
Application configuration loaded from environment variables.
All runtime settings must come through this module — no hardcoded values.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # ── Active broker ─────────────────────────────────────────────────────
    broker: str = Field(default="ibkr", description="Active broker: ibkr | alpaca")

    # ── IBKR ──────────────────────────────────────────────────────────────
    ibkr_host: str = Field(default="127.0.0.1")
    ibkr_port: int = Field(default=7497)
    ibkr_client_id: int = Field(default=1)
    ibkr_trading_mode: str = Field(default="paper")

    # ── Alpaca ────────────────────────────────────────────────────────────
    # A plain HTTPS REST API (no Gateway/TWS process to run) — one API key
    # pair per account, which is why this is the practical broker choice for
    # multi-tenant SaaS: each customer just supplies their own key pair,
    # instead of running a per-customer IBKR Gateway container.
    alpaca_api_key: str = Field(default="")
    alpaca_secret_key: str = Field(default="")
    alpaca_base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description="https://paper-api.alpaca.markets (paper) or https://api.alpaca.markets (live)",
    )

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://options_user:options_pass@localhost:5432/options_db"
    )

    # ── Trading Rules ─────────────────────────────────────────────────────
    starting_capital: float = Field(default=25000.0)
    max_daily_loss_pct: float = Field(default=0.02)
    max_weekly_loss_pct: float = Field(default=0.05)
    max_monthly_loss_pct: float = Field(default=0.10)
    max_drawdown_pct: float = Field(default=0.15)
    max_concurrent_positions: int = Field(default=5)
    max_trades_per_day: int = Field(default=6)
    max_consecutive_losses: int = Field(default=3)
    cooling_off_hours: int = Field(default=24)
    capital_preservation_threshold: float = Field(default=0.85)
    # Margin utilization thresholds (maintenance_margin / net_liquidation).
    # >= warn → amber on the Risk dashboard; >= critical → new trades blocked.
    margin_warn_pct: float = Field(default=0.50)
    margin_critical_pct: float = Field(default=0.80)

    # ── Paper visibility mode ─────────────────────────────────────────────
    # Lets the app generate more activity in paper mode so the operator can
    # confirm scans, execution, and trade history without weakening live rules.
    paper_trade_visibility_mode: bool = Field(default=False)
    paper_visibility_signal_score_threshold: float = Field(default=0.35)
    paper_visibility_signal_score_preservation_mode: float = Field(default=0.55)
    paper_visibility_equity_min_confidence: float = Field(default=0.28)
    paper_visibility_max_daily_loss_pct: float = Field(default=0.08)
    paper_visibility_max_weekly_loss_pct: float = Field(default=0.15)
    paper_visibility_max_monthly_loss_pct: float = Field(default=0.25)
    paper_visibility_max_drawdown_pct: float = Field(default=0.30)
    paper_visibility_max_trades_per_day: int = Field(default=20)
    paper_visibility_max_consecutive_losses: int = Field(default=8)
    paper_visibility_cooling_off_hours: int = Field(default=1)
    paper_visibility_capital_preservation_threshold: float = Field(default=0.65)

    # ── Equity signal settings ────────────────────────────────────────────
    # The real, current Nasdaq-100 constituent list (102 tickers, including
    # both GOOGL/GOOG share classes) — fetched live from
    # https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies rather than
    # hand-picked, and spot-checked against yfinance to confirm every symbol
    # actually resolves to a real, currently-traded instrument. Previously an
    # ad-hoc ~59-name list (mega-cap tech/financials/healthcare/etc, hand
    # assembled); this replaces it with an objective, sourced index rather
    # than a curated guess. Confirmed safe at this size (~1.7x the prior
    # list): the scan loop's bounded concurrency (equity_scan_concurrency
    # below) means wall-clock time scales with ticker-count/concurrency, not
    # ticker count alone — a live production options scan of 50 symbols
    # completed in ~47s, well under the 240s scan-cycle guard, so ~102
    # comfortably fits. Going all the way to the full S&P 500 was considered
    # separately and rejected: that needs a genuinely different bulk-data
    # architecture, not a config change.
    equity_watchlist: str = Field(
        default=(
            "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,ALAB,ALNY,AMAT,AMD,AMGN,AMZN,APP,"
            "ARM,ASML,AVGO,AXON,BKNG,BKR,CCEP,CDNS,CEG,CMCSA,COST,CPRT,CRWD,"
            "CRWV,CSCO,CSX,CTAS,DASH,DDOG,DXCM,EXC,FANG,FAST,FER,FTNT,GEHC,"
            "GILD,GOOG,GOOGL,HON,HONA,IDXX,INTC,INTU,ISRG,KDP,KHC,KLAC,LIN,"
            "LITE,LRCX,MAR,MCHP,MDLZ,MELI,META,MNST,MPWR,MRVL,MSFT,MSTR,MU,"
            "NBIS,NFLX,NVDA,NXPI,ODFL,ORLY,PANW,PAYX,PCAR,PDD,PEP,PLTR,PYPL,"
            "QCOM,REGN,RKLB,ROP,ROST,SBUX,SHOP,SNDK,SNPS,SPCX,STX,TER,TMUS,"
            "TRI,TSLA,TTWO,TXN,VRTX,WBD,WDAY,WDC,WMT,XEL"
        )
    )
    equity_signal_interval_minutes: int = Field(default=15)
    # Symbols scanned per background-scan tick. 0 = scan the whole watchlist
    # every cycle (recommended) — the previous fixed 5-per-tick rotation meant
    # most symbols only got a fresh scan every 45 min (3 ticks to cover 13
    # symbols), cutting the number of distinct opportunities that ever got a
    # chance to clear the confidence bar. Widening this changes how many
    # candidates get evaluated, not how good a candidate has to be.
    equity_scan_window_size: int = Field(default=0)
    # Bounded parallelism for the per-ticker scan loop (bars fetch + live
    # quote + scoring). At 8 concurrent tickers, ~59 symbols clears in well
    # under a minute of wall-clock time instead of ~90s+ sequential, while
    # keeping simultaneous IBKR market-data requests far below its pacing
    # limits — a burst of 8, not 59, in flight at once.
    equity_scan_concurrency: int = Field(default=8)
    equity_min_confidence: float = Field(default=0.62)
    # Paper mode uses a lower confidence threshold to accumulate trade data
    # for ML model training. Set equal to equity_min_confidence for live.
    equity_min_confidence_paper: float = Field(default=0.45)
    equity_min_risk_reward: float = Field(default=1.80)
    earnings_gate_days: int = Field(default=3)
    max_equity_positions: int = Field(default=5)
    max_options_positions: int = Field(default=5)

    @property
    def effective_equity_min_confidence(self) -> float:
        """Use lower thresholds in paper mode to build training data faster."""
        if self.paper_visibility_active:
            return self.paper_visibility_equity_min_confidence
        if self.is_paper_trading:
            return self.equity_min_confidence_paper
        return self.equity_min_confidence

    # ── Order execution ───────────────────────────────────────────────────
    # When True, orders are only submitted during US regular trading hours
    # (09:30–16:00 ET, Mon–Fri, excluding holidays). The app still runs 24/7 —
    # only order submission pauses outside RTH and resumes automatically at the
    # open. Set False to allow order attempts at any time (not recommended:
    # options don't trade after hours and equity fills are poor).
    market_hours_only: bool = Field(default=True)
    # Spread limit price multiplier applied to estimated net credit.
    # 1.0 = submit at mid (best fill rate). 0.90 = accept 10% less credit.
    # Lower values → more fills, lower credit received.
    limit_price_aggression: float = Field(default=1.0)
    # Seconds to wait for a fill before cancelling and retrying at a lower price.
    fill_timeout_seconds: int = Field(default=60)
    # How much to lower the limit price (in dollars) on each retry.
    retry_price_step: float = Field(default=0.05)
    # Maximum number of cancel-and-retry attempts per order.
    max_order_retries: int = Field(default=2)
    # Equity limit retries reprice by a percentage of the current limit
    # (options use a flat $ step; equities span too wide a price range for that).
    equity_retry_step_pct: float = Field(default=0.003)

    # ── Execution test mode (PAPER validation) ────────────────────────────
    # When True, the quality/frequency guards are bypassed so the system will
    # actually place trades — used to verify the execution→recording→UI pipeline
    # end-to-end. The HARD safety rails still apply (kill switch, account/paper
    # guard, market hours, margin-critical, sizing, duplicate guard). NEVER leave
    # this on for a real run — it disables the profitability filters.
    execution_test_mode: bool = Field(default=False)

    # ── Step 8: portfolio gate on `_execute_signal` ───────────────────────
    # Concentration + max positions + heat-high. Rollback: set false.
    execution_portfolio_gate: bool = Field(default=True)
    # Greeks delta/vega caps — OFF by default (miscalibrated for live spreads).
    execution_enforce_portfolio_greeks: bool = Field(default=False)
    # When True and max concurrent positions is hit, close N equity positions
    # (worst quality score/confidence among non-winners — see
    # position_rotation_winner_pnl_floor) to free a slot for a new equity
    # entry. Off by default — money-path auto-close.
    position_rotation_on_max: bool = Field(default=False)
    # LEGACY / VESTIGIAL as of 2026-08-28. Read at exactly one production
    # line — position_rotation.py's rotate_for_blocked_entry(), which has no
    # production callers since Stage 2b was changed to raise a ROTATION_REVIEW
    # instead of closing. The approval path proposes exactly ONE incumbent by
    # design (one slot freed needs one close; this setting's value of 2
    # over-rotated every time the old path fired), so nothing reachable
    # consults it. Kept, not deleted, until the legacy path is confirmed
    # permanently unreachable and removed as a unit.
    position_rotation_closes: int = Field(default=2)
    # Positions with unrealized P&L above this (dollars) are never rotation
    # targets — Winner Protection floor. A position must be at or below this
    # to even be eligible for closure; it never trades off against quality
    # score or confidence.
    position_rotation_winner_pnl_floor: float = Field(default=0.0)
    # How far the challenger's composite must exceed the incumbent's before a
    # replacement is even recommended (0-100 scale). Two heuristics differing
    # by a point or two is noise, and acting on noise churns capital and pays
    # spread twice. Raise it to make reviews rarer and more decisive; 0 would
    # recommend on any positive difference and is not advised.
    rotation_review_materiality_margin: float = Field(default=15.0)
    # Hours after ANY position close (stop, target, manual, rotation) before
    # the same (underlying, asset class) can be re-entered — stops a name
    # that just got stopped out from being whipsawed right back in. 0 =
    # disabled (skips the check entirely, no DB query). 2h spans several
    # scan cycles (equity every 15min, options every 30min) without
    # suppressing a legitimate same-day re-entry.
    position_cooldown_hours: int = Field(default=2)

    # ── Reconciliation: auto-adopt untracked broker positions ─────────────
    # When the periodic reconciler (_reconcile_positions, main.py) finds a
    # live equity position at the broker with no matching open Trade row,
    # write one in automatically (strategy="adopted_untracked") so it stops
    # being invisible to every DB-derived guardrail (max_positions,
    # concentration, heat — see execution_portfolio_gate.py). Confirmed
    # live in production 2026-08-26: several untracked equity positions
    # consumed real margin/risk capacity while every position-count check
    # believed far fewer positions were open. Options positions are
    # deliberately NOT auto-adopted — reconstructing a 2-leg spread's
    # short/long strikes from raw broker legs is a real, harder problem
    # (see close_options_trade's own leg-reconstruction design); an
    # untracked options position is logged for manual review instead.
    # Rollback: set false.
    reconciliation_auto_adopt_untracked: bool = Field(default=True)
    # When the reconciler finds an open Trade row whose tracked quantity
    # disagrees with the broker's live quantity for that underlying,
    # correct the DB row to match the broker (source of truth) -- confirmed
    # live 2026-08-26: MRVL/SNDK rows still held stale quantities from
    # before the close_equity_trade() sizing bug was fixed (commit
    # 1cc3eb8); that fix stops new mismatches but never retroactively
    # corrected rows it had already damaged. Only corrects when there is
    # exactly one open equity Trade row for the ticker -- ambiguous
    # (multiple open rows, or an options position) cases are skipped and
    # logged for manual review. Rollback: set false.
    reconciliation_auto_correct_quantity: bool = Field(default=True)

    # ── AI Signal Scorer ──────────────────────────────────────────────────
    signal_score_threshold: float = Field(default=0.65)
    signal_score_preservation_mode: float = Field(default=0.80)
    model_path: str = Field(default="ml/model_registry/signal_scorer_v1.pkl")
    retrain_schedule: str = Field(default="monthly")

    # ── Security ─────────────────────────────────────────────────────────
    secret_key: str = Field(default="", description="API secret key for admin endpoints")
    # ── Multi-tenant integrations (disabled until configured) ───────────
    clerk_publishable_key: str = Field(default="")
    clerk_secret_key: str = Field(default="")
    clerk_jwt_key: str = Field(default="")
    stripe_secret_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")
    credential_encryption_key: str = Field(default="", description="Fernet key from a managed secret store; never expose to clients")
    # Kill-switch reset authorization. Empty = reset disabled until configured.
    # Never ship a default that is also hardcoded in the frontend bundle.
    kill_switch_reset_code: str = Field(
        default="",
        description="Authorization code required to reset the kill switch (env KILL_SWITCH_RESET_CODE)",
    )

    # ── AI Research Assistant (provider-agnostic; Gemini default, free tier) ──
    llm_provider: str = Field(default="gemini", description="gemini | anthropic | auto")
    gemini_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    llm_model: str = Field(default="", description="Override model id; blank = provider default")

    # ── Alerts ────────────────────────────────────────────────────────────
    sendgrid_api_key: str = Field(default="")
    alert_email: str = Field(default="")
    log_level: str = Field(default="INFO")

    def get_equity_watchlist(self) -> list[str]:
        """Parse comma-separated watchlist into a list."""
        return [t.strip().upper() for t in self.equity_watchlist.split(",") if t.strip()]

    @property
    def is_paper_trading(self) -> bool:
        """True when the active broker is configured for paper trading."""
        if self.broker == "alpaca":
            return "paper" in self.alpaca_base_url.lower()
        return self.ibkr_trading_mode.lower() == "paper"

    @property
    def paper_visibility_active(self) -> bool:
        """Relaxed paper-only profile for generating observable app activity."""
        return self.is_paper_trading and self.paper_trade_visibility_mode

    @property
    def effective_signal_score_threshold(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_signal_score_threshold
        return self.signal_score_threshold

    @property
    def effective_signal_score_preservation_mode(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_signal_score_preservation_mode
        return self.signal_score_preservation_mode

    @property
    def effective_max_daily_loss_pct(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_max_daily_loss_pct
        return self.max_daily_loss_pct

    @property
    def effective_max_weekly_loss_pct(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_max_weekly_loss_pct
        return self.max_weekly_loss_pct

    @property
    def effective_max_monthly_loss_pct(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_max_monthly_loss_pct
        return self.max_monthly_loss_pct

    @property
    def effective_max_drawdown_pct(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_max_drawdown_pct
        return self.max_drawdown_pct

    @property
    def effective_max_trades_per_day(self) -> int:
        if self.paper_visibility_active:
            return self.paper_visibility_max_trades_per_day
        return self.max_trades_per_day

    @property
    def effective_max_consecutive_losses(self) -> int:
        if self.paper_visibility_active:
            return self.paper_visibility_max_consecutive_losses
        return self.max_consecutive_losses

    @property
    def effective_cooling_off_hours(self) -> int:
        if self.paper_visibility_active:
            return self.paper_visibility_cooling_off_hours
        return self.cooling_off_hours

    @property
    def effective_capital_preservation_threshold(self) -> float:
        if self.paper_visibility_active:
            return self.paper_visibility_capital_preservation_threshold
        return self.capital_preservation_threshold


settings = Settings()
