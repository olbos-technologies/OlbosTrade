# 0DTE strategy bank — pre-registration

**Status: hypotheses. Nothing here has been tested, and nothing here is
approved to trade.** 0DTE autopilot is blocked by policy
(`trade_desk.py`, stage 1b2 `0dte_autopilot_disabled`) and stays blocked until
the evidence below exists and the owner lifts it.

This file exists so the rules are frozen *before* any data is looked at. A rule
written after seeing results is a fit, not a test.

## Where the rules come from

Three independent write-ups were collected on 2026-11-01 (a volatility-filtered
credit-spread plan, an opening-range-breakout pullback plan, and a
regime-switched spread plan). They agree on structure and disagree on
parameters. The disagreements are listed as a **grid to test**, not argued.

Public evidence is thin. Retail 0DTE traders lost money on average in the one
large study found (Beckmeyer, Branger & Gayda, data to Sep 2023), multi-leg
trades did better than single-leg but still lost on average, and the
iron-condor hit rates published by vendors are their own undisclosed
backtests. Treat every number in the source plans as unverified.

## The target is a ceiling, not an edge

0.3–0.5% a day is about +113% to +250% a year compounded. A daily target tells
the bot when to **stop**; it cannot supply the edge. The metric that decides
everything is **net expectancy per trade after costs**, with its confidence
interval.

## Shared rules (all candidates)

- Defined risk only. No naked or unlimited-risk legs (charter).
- Skip the day: FOMC, CPI, NFP and other scheduled macro releases; stale or
  one-sided quotes; kill switch active.
- No entry in the first 15 minutes; no new 0DTE entry in the last 30
  (already enforced at `trade_desk.py` stage 1b3).
- Flat by 15:30 ET. No overnight exposure.
- Limit orders at mid, walked toward the natural price in small steps. No
  market orders for entry. Reject on bid/ask width above the liquidity gate.
- Underlyings: SPY and QQQ (charter-supported). SPX is a separate owner
  decision (not on the supported list; cash-settled/European, 60/40 tax).
- Daily stop on realised + open P&L at both -0.5% and the target; weekly -2%.
  Existing guardrails (2% daily, 5% weekly, 10% monthly, 5 concurrent) stay
  the outer limit and are never loosened by a candidate.

## Candidates

| ID | Structure | Entry | Regime filter |
|---|---|---|---|
| A | Iron condor or one-sided credit spread, shorts 8–12 delta | 10:00–11:30 ET | VIX 13–28, overnight gap < 0.7%, IV/RV > 1.0, range day |
| B | SPY/QQQ shares, opening-range breakout then pullback retest | 09:45–11:00 ET | SPY and QQQ agree vs VWAP, breakout-bar volume > 1.2x 20-day median |
| C | B's direction call picks the structure: range → A; trend → debit vertical | per A / B | classify at 10:00 ET |
| D | SPY/QQQ shares, short-horizon mean reversion: enter when the 5/15-min z-score vs VWAP or EMA(20) is ≤ -1.8..-2.2 (long) or ≥ +1.8..+2.2 (short) | 10:00–15:00 ET | ATR(14) between 0.8x and 2.5x its 20-period median; 1H trend not strongly against; volume > 0.7x 20-period average |

Range day: opening range (09:30–10:00) < 0.35% of price, price near VWAP,
flat VWAP slope. Trend day: break of the range on above-average volume,
holding the correct side of VWAP. Neither: no trade.

Candidate D is the transferable core of a fourth plan written for crypto
perpetuals and forex (3-5x leverage on Binance/Bybit/Hyperliquid). **Those
venues, instruments and leverage are outside the charter and the IBKR broker
scope and are not proposed here**; only the signal logic is ported to
charter-supported ETFs. Exits for D: target 0.35-0.55% of price (or 1.2-1.5R),
stop 0.25-0.40% (or 0.8-1.0x ATR), time stop at 45-90 minutes, move the stop to
breakeven plus costs after +0.25%. Risk 0.4-0.7% of equity per trade, at most
2-3 concurrent, shares only, no leverage beyond what the account's existing
exposure cap allows.

### Candidate D, as implemented for testing (QQQ primary, SPY secondary)

Shares only, regular hours only, flat by 15:55 ET. Rules are in
`backend/scripts/vwap_reversion_backtest.py`; its docstring lists every
conservative assumption (entry on the next bar's open, stop wins a same-bar
touch, gap-through fills at the open, a target must trade through by a tick).
Choices the source plan left open, fixed here before any real data is seen:

- z-score = (close - session VWAP) / rolling std of that deviation within the
  day (window 48 bars, minimum 8). A stable std needs ~40 minutes of bars, so
  the earliest signal is about 10:10 ET, not the 09:45 the plan names.
- Windows (signal-bar end): 09:45-11:30 and 13:30-15:30; lunch skipped.
- Trend filter: slope of the 20-period EMA of **completed** hourly closes, long
  only if slope >= 0, short only if <= 0; "none" is in the grid as an ablation.
- ATR(14) on 5-minute bars must be 0.7x-2.2x its 20-bar median; volume > 0.6x
  its 20-bar mean. Breakeven-plus-fees stop after +0.22%.
- Costs: $0.01/share/side slippage and $0.005/share/side commission, re-run at 2x.

**Sizing decides whether the target is reachable at all.** A winning trade
moves price about 0.4%, so it adds `0.4% x (notional / equity)` to the account:

| Notional / equity | Gain from one 0.40% winner |
|---|---|
| 0.08x (equity sizer's per-position default) | 0.032% |
| 0.30x (charter's exposure figure) | 0.12% |
| 1.00x (fully invested cash) | 0.40% |
| 2.00x (margin) | 0.80% |

At the sizer's current 8% cap, 0.3% a day would need about nine winners a day;
at the charter's 30% about three. Reaching it with one or two trades needs
roughly 0.75x-1.0x or more, which is a charter and sizing decision, not a
signal property. The backtest reports the same trades at all four caps so the
cost of each choice is visible. (`equity_signal_engine.max_position_pct` is
the sizer's default; I did not check every override on the live path.)

**Running it** (read-only; neither script can place an order):

```bash
python scripts/intraday_bars_fetch.py bars.csv --client-id 17 --symbols QQQ SPY --start 2023-01-01
python scripts/vwap_reversion_backtest.py bars.csv
```

The exporter uses a read-only IBKR session and its own client id, about an hour
for three years of two symbols, resumable. Verified: the logic and tests, on
synthetic bars. **Not verified:** the gateway's pacing limits and data
permissions; run it once and check the row counts.

The synthetic-data check is only a machinery check. On random-walk bars with
no edge by construction, the best of 108 cells looked positive in the
selection period and negative in the holdout, which is the over-fitting the
split exists to catch. It says nothing about real prices.

### Candidate D+ — signal-quality filter on D (from a fifth, "hybrid" plan)

A plan proposed layering quantitative components on D: a signal-quality score,
regime detection, dynamic sizing, a QQQ/SPY portfolio book, and execution
models. Only the first is built, because it is the one the plan itself ranks
first and the only one testable on the data we can export.
`backend/scripts/vwap_quality_score.py`:

- Takes the trades D takes and asks whether a model trained **only on earlier
  trades** can pick the better ones better than keeping the same number at
  random.
- Plain logistic regression (numpy, ridge), 8 features oriented by trade
  direction: |z|, ATR ratio, relative volume, hourly-trend slope, overnight gap,
  3-bar return, time of day (sin/cos). Label: net positive after costs.
- **Fixed before any data is seen:** L2 = 1, keep the top 50%, at least 300
  training trades. One configuration, so there is no multiple testing to hide in.
- Walk-forward by calendar month; the keep threshold is a percentile of the
  *training* scores. Reported with out-of-sample AUC, a 5,000-draw permutation
  p-value against random subsets of the same size, standardised coefficients,
  and the daily result at the four exposure caps (all trades vs kept only).
- Not gradient boosting: a few years of one ETF give far too few labelled trades
  to train trees without over-fitting.
- Tests: leakage guards (a month's scores cannot depend on that month or later),
  a power check (planted skill is found, no skill is not called a discovery),
  and a threshold-leak guard. Deliberately letting the model train on the month
  it scores made all four no-skill datasets report a "discovery".

**Not built, and why:**

| Layer | Status |
|---|---|
| Regime detection (HMM / clustering) | Needs its own pre-registration; adding it on top of an unvalidated D multiplies the ways to over-fit. Next in the plan's order. |
| Dynamic sizing / Kelly / volatility targeting | Depends on the sizing and exposure-cap decision above; sizing is the binding constraint, not the signal. |
| QQQ+SPY correlation-aware book, relative-value overlay | Needs both legs' results first; the two are highly correlated. |
| Predictive slippage, order-book imbalance, smart routing / iceberg | The exporter provides trade bars only: no order book, and live commissions and fills are not recorded. Iceberg orders are irrelevant at these sizes. |

### Parameter grid (every cell is run; the count of cells is reported)

| Parameter | Values |
|---|---|
| Spread width | 1, 2, 5, 10 points |
| Short delta | 8, 10, 12, 16 |
| Take-profit (credit) | 50%, 65%, 70% of credit |
| Stop (credit) | 1x, 1.5x, 2.5x, 3x credit, or 5-min close through short strike |
| Time exit | 15:00, 15:30 |
| Debit-vertical reward:risk | 1:1, 1:1.5, 1:2 |
| ORB retest tolerance | 0.05, 0.10, 0.15 x ATR(14) on 5-min |

## Break-even arithmetic (before costs)

Win rate needed so that expectancy is zero, if every winner hits the take-profit
and every loser hits the stop exactly. Real stops on 0DTE slip through the
trigger, so real break-even is higher.

| Design | Win | Loss | Break-even win rate |
|---|---|---|---|
| Credit, TP 70% / stop 2.5x credit | 0.70 c | 1.50 c | 68.2% |
| Credit, TP 70% / stop 3x credit | 0.70 c | 2.00 c | 74.1% |
| Credit, TP 50% / stop 1x credit | 0.50 c | 1.00 c | 66.7% |
| Debit vertical, 1 : 1.5 | 1.5 R | 1 R | 40.0% |
| Shares D (win 0.45% / loss 0.325% of price, 0.05% round-trip cost) | 0.40 % | 0.375 % | 48.4% (51.5% with 0.05% more stop slippage) |

(`c` = credit received.) The source plans quote green-day rates of 60–65% for
the 50%/1x design, which is **below** its 66.7% break-even. Tighter stops are
hit more often by intraday noise, so the win rate and the payoff trade off
against each other. That trade-off is the empirical question the grid answers.

Candidate D has the most forgiving payoff of the four (break-even near 48%
versus 67-74% for the credit designs), so a 55-65% win rate would give about
+0.08% to +0.20% of equity per trade, or roughly 0.2-0.5% a day over 2-3
trades. The whole case rests on a **55-65% win rate together with winners
larger than losers**, which mean-reversion systems rarely deliver at once
(they usually trade a high win rate for small winners). That is the number the
out-of-sample test must show, with its confidence interval.

### Costs are the first thing to kill it

A 1–2 point wide condor at 8–12 delta collects a small credit (unverified
estimate: tens of cents). Slippage of 2–5 cents per leg across 4 legs is
8–20 cents on entry alone, plus commissions on up to 6 legs round trip. At
narrow widths the costs can consume most of the credit. The harness must model
fills from real bid/ask and report results at 1x, 1.5x and 2x assumed costs.

## Consistency flags in the source plans

- 0.4–0.5% risk per trade with a -0.5% daily stop means one full loss ends the
  day; a second trade is only possible after a win.
- Expecting 10–20% drawdowns conflicts with the charter's 10% monthly limit,
  which suspends trading. Either the expectation or the limit is wrong.
- `risk_manager` sizes contracts as target risk / max loss per spread. If that
  is the full-width loss, 2% per trade allows about 2 condors per $100k
  (~0.08% a day at 0.60 credit), not the 10 the source plan assumes. **To
  verify at the call site before any sizing work.**

## Validation gates (all must pass; failing any is a result, not a setback)

1. **Data:** several years of intraday options history with bid/ask for the
   traded underlyings, across trend, range and high-volatility periods.
   Not yet sourced.
2. **Chronological split:** parameters chosen on the earlier period, judged on
   a later untouched period. Number of grid cells tried is reported.
3. **Net expectancy > 0 out of sample** at 1x costs and still > 0 at 2x costs.
4. **Not carried by a few days:** results with the best 5 days removed.
5. **Sample size:** at least 200 out-of-sample trades. For scale, the 95%
   confidence band on a 70% win rate is about ±14 points at 40 trades,
   ±9 at 100, ±6 at 250.
6. **Shadow mode** (read-only recorder, no orders) verifies fills and costs
   against the model. It cannot prove an edge: a month may contain no tail day.
7. **Copilot only** after 1-6, with the owner approving each entry. Autopilot
   for 0DTE only by explicit owner decision.

## Prerequisites in the system

- Live options exit engine (profit target, stop, time stop). `check_exit`
  currently runs only in the backtester; a short-dated spread cannot run
  without it.
- Real commissions captured on live fills (not recorded today).
- Intraday bar/VWAP source for candidates B and C.

## Owner decisions this file does not make

Lifting the 0DTE autopilot block, admitting SPX, the definition of per-trade
risk for spreads, and whether to buy historical options data.
