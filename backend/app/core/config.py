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

    # ── Regime hysteresis ─────────────────────────────────────────────────
    # How many consecutive classifications must agree before a regime CHANGE
    # is adopted. The classifier is stateless and several of its decisions sit
    # on knife-edges of continuous inputs — a 1bp move in the 5-day return
    # flips normal_mean_revert -> high_vol_trending, which removes iron
    # condors, cuts size to 75% and raises the signal bar. Reclassification
    # runs every 30 min against a still-forming daily bar, so that boundary is
    # re-sampled ~13x a session. Measured against the shipped classifier on
    # simulated ordinary markets: ~97 changes/yr unguarded, ~44 at 3.
    # CRISIS always bypasses this — risk-off is never delayed.
    # 1 disables the guard (pre-guard behaviour).
    regime_confirm_readings: int = Field(default=3, ge=1, le=20)

    # ── Proxy trust ───────────────────────────────────────────────────
    # Shared secret proving a request came through the frontend nginx, which
    # is the only thing that overwrites X-Forwarded-For with the address it
    # actually observed. rate_limit.client_ip() believes that header ONLY
    # when this matches.
    #
    # Replaces --proxy-headers --forwarded-allow-ips=* (issue #60). uvicorn's
    # middleware can only decide by PEER ADDRESS, and the backend shares the
    # external docker_default network with Caddy and the IBKR gateway — it has
    # to, that is where IBKR_HOST resolves — so peer address cannot separate
    # the frontend from anything else on that network. A secret can, and it
    # keeps working if the topology changes.
    #
    # Empty = do not trust the header at all. Safe, but it puts every caller
    # behind the proxy in one rate-limit bucket, so docker-compose.hetzner.yml
    # requires it rather than defaulting it.
    trusted_proxy_secret: str = Field(default="")

    # ── Broker credential encryption ──────────────────────────────────
    # A Fernet key used to encrypt users' broker API credentials at rest.
    # Generate with:
    #     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    #
    # DELIBERATELY SEPARATE FROM SECRET_KEY. That one is the operator API key
    # and gets rotated — routinely, and urgently after any suspected exposure.
    # Deriving this from it would mean a rotation silently renders every
    # stored broker credential undecryptable, with the first symptom being
    # orders failing during market hours. Incident response must not destroy
    # data as a side effect.
    #
    # Empty = this deployment cannot store broker credentials, and the routes
    # that would store them answer 503 rather than falling back to plaintext.
    # Defaulting to empty is deliberate, and the opposite of the call made for
    # AUTH_ENABLED in #74: that one is required because a missing value is
    # indistinguishable from "off" on a security control that was meant to be
    # on. This one has a safe empty meaning — the feature is unavailable, and
    # everything else on the install works exactly as it did before.
    broker_encryption_key: str = Field(default="")

    # ── Equity signal geometry ────────────────────────────────────────
    # stop = entry ∓ ATR×stop_mult, target = entry ± ATR×target_mult.
    #
    # These were hardcoded in equity_signal_engine.py. They are settings now
    # because they are the one thing in the signal path that should be chosen
    # from measured outcomes rather than assumed, and re-measuring after every
    # change should not need a code edit and a deploy.
    #
    # The ratio between them is NOT where an edge comes from, which is worth
    # stating because it is easy to assume otherwise: for barriers at +a and
    # −b, the hit rate needed to break even is b/(a+b), and the hit rate a
    # coin flip achieves is also b/(a+b). Equal for every choice of a and b.
    # Moving the target does not manufacture margin; it decides how much of
    # the move you try to capture, and how often you run out of time first.
    #
    # What makes the choice bite is the 20-day expiry in
    # signal_outcome_tracker.py: over N days a random walk covers roughly
    # ATR×√N, so at 20 days a 4×ATR target sits near the edge of the typical
    # range while a 2×ATR stop sits well inside it. Slow winners expire;
    # losers resolve. Check by_mfe_bucket_r against target_distance_r in
    # /api/signal-research/outcomes before changing these: if favourable
    # excursion rarely reaches the target's distance in R, the target is out
    # of reach and is converting winners into expiries.
    #
    # CHANGING THESE ONLY AFFECTS NEW SIGNALS. Each row persists the stop and
    # target prices it was generated with, and resolution reads those, so a
    # retune cannot relabel history. That property is why the expiry horizon
    # is deliberately NOT a setting here — see signal_outcome_tracker.py.
    #
    # Off 2:1 these stop being cosmetic: risk_reward, and the opportunity
    # components derived from it, are only confidence-determined while the
    # ratio is fixed. See the note on SignalOutcome.opportunity_score.
    equity_stop_atr_multiplier: float = Field(default=2.0, gt=0, le=10)
    equity_target_atr_multiplier: float = Field(default=4.0, gt=0, le=20)
    cooling_off_hours: int = Field(default=24)
    capital_preservation_threshold: float = Field(default=0.85)
    # Margin utilization thresholds (maintenance_margin / net_liquidation).
    # >= warn → amber on the Risk dashboard; >= critical → new trades blocked.
    margin_warn_pct: float = Field(default=0.50)
    margin_critical_pct: float = Field(default=0.80)

    # ── Paper → live tenure gate ──────────────────────────────────────────
    # The charter requires a paper-trading track record before any live
    # capital ("paper trade for 3 months minimum"). Enforced by
    # app.services.live_tenure_guard, which blocks live order submission
    # until both floors below are met. Applies ONLY when configured live;
    # in paper mode the gate is a no-op. Set days to 0 to disable the gate
    # entirely (a deliberate, auditable config change — not a runtime
    # override, which the charter forbids).
    live_min_paper_trading_days: int = Field(default=90)
    # A time floor alone would pass an install that sat idle for 3 months and
    # placed two trades, so require a minimum number of *finished* trades too.
    live_min_paper_closed_trades: int = Field(default=20)

    # ── Deployment capability ─────────────────────────────────────────────
    # False makes this instance structurally incapable of placing an order:
    # get_broker() returns a ReadOnlyBroker whose place_order/place_equity_order
    # raise. This is the shared tier of the hybrid tenancy model — signals,
    # research and backtests for many accounts, with execution living in a
    # separate per-tenant stack.
    #
    # Deliberately NOT the same thing as execution_mode=manual or an armed kill
    # switch: those are runtime state a request can change. This is a property
    # of the deployment, settable only in the environment. Defaults True so
    # existing single-operator installs are unaffected.
    execution_enabled: bool = Field(default=True)

    # ── Authentication (Phase 1) ──────────────────────────────────────────
    # Defaults FALSE so existing single-operator installs keep working exactly
    # as they do today (nginx Basic Auth via DASH_USER/DASH_PASS, plus the
    # X-Api-Key operator key on mutate routes). Turning it on switches the API
    # to default-deny: every route requires a logged-in session except an
    # explicit allowlist.
    #
    # Sequence this deliberately — ship app auth, verify it with Basic Auth
    # still in front, and only then remove Basic Auth. Removing the outer wall
    # first makes app auth the only thing between the internet and a trading
    # API on its first day in production.
    auth_enabled: bool = Field(default=False)
    # ge=1 is not pedantry: AUTH_SESSION_HOURS=0 made login return 200 while
    # storing an already-expired session and sending Max-Age=0, so the browser
    # dropped the cookie and every account was locked out with no error
    # anywhere. Refusing to start is the kinder failure.
    auth_session_hours: int = Field(default=12, ge=1, le=720)
    # Set false only for local HTTP development; the session cookie must carry
    # Secure in any deployment reachable over a network.
    auth_cookie_secure: bool = Field(default=True)

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
    # ── Crypto (phase 1: read-only signals, no execution) ─────────────────
    # Phase 1 generates crypto signals and records their forward outcomes so
    # there is a real, dated sample with a denominator before any capital is
    # committed. Nothing here can place an order: run_crypto_scan() has no
    # route to handle_signal(), so this flag controls a measurement job, not a
    # trading permission. It therefore defaults ON — a read-only scan that
    # never runs collects nothing, and the collection is the deliverable.
    crypto_enabled: bool = Field(default=True)
    # Symbols in the internal DASH form (see crypto_signal_engine.
    # normalize_crypto_symbol) — which is also the yfinance ticker and the
    # value stored in signal_outcomes.ticker.
    #
    # This list is the intersection of two constraints, not a popularity
    # ranking. (1) Every name is tradable on Alpaca's US crypto venue, so a
    # later execution phase can act on the same population this phase
    # measures — signals for an instrument the broker cannot trade would
    # measure something unactionable. That is why liquid non-Alpaca names
    # (ADA, XLM) are absent. (2) Every name clears
    # MIN_REPRESENTABLE_PRICE, so no row collapses under the Numeric(12, 4)
    # price columns — which is why SHIB and PEPE are absent despite being on
    # Alpaca.
    crypto_watchlist: str = Field(
        default=(
            "BTC-USD,ETH-USD,SOL-USD,XRP-USD,AVAX-USD,LINK-USD,"
            "DOT-USD,LTC-USD,DOGE-USD,BCH-USD,UNI-USD,AAVE-USD"
        )
    )
    # Crypto trades 24/7, so unlike the equity scan this cadence is not shaped
    # by a session — it is purely how stale a daily-bar signal is allowed to
    # get. Daily bars are the input, so anything under ~15 min would re-derive
    # identical indicators; the (ticker, action, UTC-day) dedup in
    # record_signal means the extra passes would write nothing either way.
    crypto_signal_interval_minutes: int = Field(default=30)
    crypto_scan_concurrency: int = Field(default=6)
    # Deliberately NOT tied to effective_equity_min_confidence, and not lower
    # than it. A crypto signal is scored with orderflow_score at its neutral
    # default because no crypto orderflow feed exists (see
    # crypto_signal_engine's docstring), so it clears this bar on strictly
    # less evidence than an equity signal clearing the same number. A higher
    # floor is what keeps the recorded cohort from being dominated by weak
    # setups that only look comparable.
    crypto_min_confidence: float = Field(default=0.60)
    # Sizing for the recorded trade plan. Phase 1 never submits an order, so
    # this only affects the advisory position_size/shares shown alongside a
    # signal — but quoting equity sizing for an asset that routinely moves
    # 5-10% in a day would be misleading the moment anyone reads it.
    crypto_max_position_pct: float = Field(default=0.03)
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

    def get_crypto_watchlist(self) -> list[str]:
        """
        Parse the crypto watchlist into canonical internal symbols.

        Normalised rather than merely upper-cased: an operator overriding
        CRYPTO_WATCHLIST is as likely to write ``BTC/USD`` or ``BTCUSD`` as
        ``BTC-USD``, and three shapes of one instrument would split into three
        populations that the (ticker, action, day) dedup cannot see across.
        Duplicates that collapse to the same symbol are dropped, order kept.
        """
        from app.services.crypto_signal_engine import normalize_crypto_symbol

        seen: set[str] = set()
        out: list[str] = []
        for raw in self.crypto_watchlist.split(","):
            if not raw.strip():
                continue
            symbol = normalize_crypto_symbol(raw)
            if symbol not in seen:
                seen.add(symbol)
                out.append(symbol)
        return out

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
