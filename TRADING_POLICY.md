# OlbosTrade — Trading Policy & Charter

OlbosTrade is an institutional-grade AI quantitative options trading system.

**Objective:** maximize long-term *risk-adjusted* returns while minimizing
drawdowns. **Not** to maximize the number of trades. **Capital preservation
comes before profit.**

> Trade ONLY when a statistical edge exists. If no high-quality trade exists,
> remain in **CASH**. Never force trades. Cash is a valid position. One
> excellent trade beats ten mediocre ones. The goal is to maximize expected
> value over thousands of trades — not to win every trade.

This document is the system's charter. Each section notes **how it is enforced
today**, so the policy is auditable rather than aspirational.

---

## System rules (always in force)
Never chase · never FOMO · never revenge-trade · never violate risk rules ·
never average down a loser · no naked options · no unlimited-risk strategies ·
no martingale · always preserve capital · consistency over excitement.

*Enforced by:* kill switch, guardrails (`guardrails.py`), the trade-frequency
controller's EV/POP/quality gates, the paper/account guard, and the
duplicate-position guard. Averaging-down and naked/unlimited strategies are not
implemented.

## Supported assets
- **ETFs:** SPY, QQQ, DIA, IWM
- **Mag-7:** AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA
- **Future expansion:** GLD, TLT, XLE, XLF, SMH, SOXX

*Status:* the **equity** watchlist covers these. **Options spread scanning covers
the full equity watchlist** (SPY, QQQ, and all ~59 Nasdaq-100 members); the
background options scanner ranks candidates by quality score and routes the
best-scoring spread through execution each cycle. The **CSP/covered-call
screener** runs the same watchlist via `/api/options/csp`.

## Supported strategies
Bull put spread · bull call (debit) spread · bear call spread · bear put spread ·
cash-secured put · covered call. **No** naked / unlimited / martingale.

*Status:* bull-put, bear-call, iron-condor, bull-call-debit, **bear-put spread** all live.
**Cash-secured put / covered call (wheel)** live via the CSP module + `/api/options/csp` +
CSP screener.

## Trading modes
- **Manual** — generate only, never execute.
- **Copilot** — generate, wait for human approval, allow modification.
- **Autopilot** — auto-execute **only if ALL risk filters pass**.

*Status:* ✅ implemented (`execution_mode`).

## Brokerage & account management
- **Broker abstraction:** all trading goes through `BrokerInterface`.
- **Live implementation:** IBKR (paper). Account management UI scaffold exists
  (`Settings.tsx` → Profile / **Broker Integrations** / Billing).

*Status / roadmap:* single-broker (IBKR) today. **Multi-broker execution +
real broker-integration backend** (route equity vs options to the best venue;
wire the Settings → Broker Integrations tab to a live `/api/brokers` status) is
the current active roadmap — additive, IBKR stays the default.

## Risk management (hard limits — enforced)
| Limit | Charter | Enforced default | Config key |
|---|---|---|---|
| Max account risk / trade | 2% | 1–2% (mode-based) | `risk_per_trade_pct` |
| Max daily loss | 3% | **2%** (tighter) | `max_daily_loss_pct` |
| Max weekly loss | 8% | **5%** (tighter) | `max_weekly_loss_pct` |
| Max monthly drawdown | 10% | 10% | `max_monthly_loss_pct` |
| Max concurrent positions | 5 | 5 | `max_concurrent_positions` |
| Max trades / day | 3 | **6** (looser) | `max_trades_per_day` |
| Max exposure | 30% | (via sizing/heat) | — |

> Current daily/weekly caps are **tighter** than the charter (more
> conservative). They are configurable to the charter values; we keep the
> tighter defaults unless deliberately changed.

> **Trades/day is the one limit that is looser than the charter**, which
> called it a hard cap of 3 "no exceptions." The enforcement mechanism is
> unchanged — `guardrails.py` still hard-stops at the configured number — only
> the number differs, and it has been 6 for as far back as this repo's history
> goes. The plausible reason is that the charter's 3 was written for four
> spread strategies on a single underlying, whereas equity signals, options
> spreads and the CSP/covered-call screener can now each independently propose
> a trade on the same day; 6 caps blast radius without starving a
> multi-strategy desk. That rationale is reconstructed, not recorded — no
> commit or note states it. **Owner decision needed:** confirm 6 and let this
> paragraph stand as the record, or set it back to 3.

## Paper trading before live capital
The charter requires paper trading for **3 months minimum before any live
capital**. This is a hard limit like the ones above, not an intention.

*Enforced by:* `live_tenure_guard.py`, on the authoritative order path in
`_execute_signal` immediately after the account guard. With
`IBKR_TRADING_MODE=live`, order submission is blocked until **both**
`LIVE_MIN_PAPER_TRADING_DAYS` (default 90) have elapsed since the first
recorded trade **and** `LIVE_MIN_PAPER_CLOSED_TRADES` (default 20) trades have
finished. The trade-count floor exists because time alone would pass an install
that sat idle for three months and placed two trades; it is this repo's
addition, not a charter number. Manual orders are **not** exempt. In paper mode
the gate is a no-op. Fail-closed: an unreadable track record blocks live
execution rather than assuming it is sufficient.

*Known limitation:* `trades` has no per-row paper/live flag
(`trading_mode_at_entry` is the risk style, not the account type), so the gate
measures all trading history. Before the first live order that history is
entirely paper — which is exactly when the gate decides — and afterwards tenure
only grows, so the conflation is harmless. Wiping trade history resets the
track record and re-blocks live trading, which is the intended reading.

## Position sizing
Fractional Kelly · volatility (ATR) sizing · portfolio heat · correlation
adjustment. *Status:* `volatility_sizing`, `risk_manager`, `allocation_engine`
(Kelly option), portfolio-heat endpoint. Correlation adjustment: partial.

## Signals, indicators & market context
Entry only if: trend aligns · momentum confirms · volume confirms · regime
favorable · liquidity acceptable · **POP > threshold** · **EV positive**.

*Status:* ✅ equity scanner + options scorer + regime classifier + POP/EV gates.
**Chart Intelligence** (`services/chart/`) adds market bias, **multi-timeframe
alignment**, market-structure, breakout confirmation, and a setup scanner.
**Intel Hub** (`services/intel/`) adds news classification, catalyst/econ
calendar, and insider intelligence. **Smart Alerts** (`services/alerts/`) adds a
rule engine + Notification Center.

**Regime staleness:** the classifier reclassifies every 30 minutes; if it goes
more than 2 hours without a successful reclassify (`MAX_REGIME_AGE_SECONDS` in
`main.py`) — or has never classified at all — the regime resets to `UNKNOWN`
before the next equity or options scan. `UNKNOWN` is fail-open for both asset
types at reduced size (`REGIME_CONFIG[RegimeType.UNKNOWN]`), never fail-closed;
that asymmetry (equities proceeding on unknown regime, options refusing to
scan at all) was a bug, not a deliberate safety margin. Real danger is still
covered separately by the kill switch and `CRISIS` classification, both of
which remain fail-closed.

## Options analysis
IV / IV rank / greeks / POP / expected move / EV / breakeven. *Status:* ✅
(`options_intelligence`, signal scorer).

## Exit conditions
Profit target (50 / 75 / 100%) · ATR trailing stop · time stop · technical stop ·
max loss · early exit before major news. *Status:* profit targets, stop
multiplier, DTE time-stop live; ATR-trailing and news-exit are roadmap.

## AI confidence score & decision
Confidence 0–100 from trend, momentum, volatility, flow, liquidity, regime,
risk/reward, POP, EV. **Trade only above a configurable threshold.**

> **Charter default: 85%. Evaluation-phase override: lower.** OlbosTrade has no
> validated track record yet — an 85% bar would prevent nearly all trades and
> starve the system of the data needed to *prove* an edge. During the initial
> paper-evaluation phase we run a **lower threshold to gather a track record**,
> then **ratchet up toward 85%** as the edge is validated.

Decision output: `BUY | SELL | HOLD | CASH`. If confidence < threshold, risk
exceeds limits, liquidity is poor, or no edge is present → **stay in cash.**

## Learning engine
Store every trade with entry, exit, indicators, greeks, regime, win/loss,
duration, **MFE/MAE**, POP, EV; update the performance DB; retrain the ranking
model periodically. *Status:* ✅ trade recorder + MFE/MAE + Research Lab +
strategy-health + signal-scorer retrain schedule.

## Standard trade report / output (JSON)
```json
{
  "ticker": "", "strategy": "", "expiration": "", "entry": "", "exit": "",
  "stop_loss": "", "max_profit": "", "max_loss": "",
  "probability_of_profit": 0, "expected_value": 0, "confidence": 0,
  "market_regime": "", "position_size": "", "risk_level": "",
  "decision": "BUY | SELL | HOLD | CASH", "reasoning": []
}
```

---

## Focus & roadmap (the profit-first path)
The guiding priority is a **robust, profitable, consistent automated execution
system** — not feature breadth. In priority order:

1. **Validate:** paper-run the core loop and measure win rate / profit /
   drawdown per strategy & timeframe. *This is the gate for "does it make money."*
2. **Multi-broker execution + account management** (active): a broker registry,
   an Alpaca implementation behind `BrokerInterface`, asset-class routing, and a
   live `/api/brokers` status wired into Settings → Broker Integrations.
3. **Bear-put spread** and **multi-underlying options spreads.**
4. **ATR trailing / news-aware exits.**

### Frozen (built, but not on the profit-critical path)
Kept and maintained, but **not actively expanded** until the core loop is proven:
Options Flow / Unusual Activity / Flow Recommender / Income Matrix (free-data
discovery), News/Intel Hub, Smart Alerts, Symphony, Chart Workstation cosmetics.

## Requires paid real-time data (out of scope until a feed is added)
Not honestly implementable on delayed daily yfinance data:
- True intraday 1-minute confirmation on live ticks.
- Dark-pool activity, dealer positioning, gamma exposure (GEX), gamma-squeeze,
  real-time institutional flow, liquidity sweeps.

*(Free approximations exist for some — the Unusual Options Activity tab and Flow
Recommender derive from yfinance option chains, not a real OPRA tape.)*
