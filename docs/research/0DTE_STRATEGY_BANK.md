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

Range day: opening range (09:30–10:00) < 0.35% of price, price near VWAP,
flat VWAP slope. Trend day: break of the range on above-average volume,
holding the correct side of VWAP. Neither: no trade.

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

(`c` = credit received.) The source plans quote green-day rates of 60–65% for
the 50%/1x design, which is **below** its 66.7% break-even. Tighter stops are
hit more often by intraday noise, so the win rate and the payoff trade off
against each other. That trade-off is the empirical question the grid answers.

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
