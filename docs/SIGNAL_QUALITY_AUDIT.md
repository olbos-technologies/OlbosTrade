# Signal quality audit: day trading, swing, options and crypto

**Status: proposed for the owner's decision · November 19, 2026.** A static review of the current
`main` and of the measurements the repository already records. I could not query the production
database from the build environment, so every figure below is quoted from where the repository
recorded it, with that source named. Nothing in the product changes because of this document, and
it authorizes no live trading, Autopilot or new execution path.

## The request

Refocus the app on four styles (day trading, swing trading, options, crypto), with the emphasis on
signal quality, and make generated signals **at least 75% accurate**.

## Short answer

1. **The signals are not near 75% today, and the repository already proves it.** Only **7.0%** of
   resolved equity signals reached their target (`docs/mobile_trade_ideas_data_audit.md`). The score
   that gates routing does not separate winners from losers: the last skill test measured **AUC
   0.541, clustered 95% CI 0.498 to 0.585, on 9,274 signals** (`backend/scripts/confidence_skill_*`).
   That interval includes 0.5, a coin flip.
2. **"75% accurate" on its own is the wrong target, and an easy one to fake.** Win rate depends
   mostly on where the target and stop are placed, not on skill (see "Why win rate alone misleads").
   A signal set can hit 75% and still lose money.
3. **What can be committed to** is a measured standard, for each style: a signal is published only
   when its setup's forward-recorded results, after costs, show a positive expectancy whose interval
   clears zero, with its win rate and interval shown next to it. A 75% win rate can be one of the
   publication thresholds, **if** the data supports it. It may mean far fewer signals, or none for
   a while.

I recommend the third. It is the honest version of what was asked, and the app already has most of
the machinery.

## What the repository already measures

| Fact | Source |
| --- | --- |
| Every routable equity and crypto signal is recorded when generated, then resolved against later daily bars: target first, stop first, or neither within the hold window. | `backend/app/services/signal_outcome_tracker.py` |
| The recorder once stored the same setup about 45 times a day (70,798 rows for about 1,568 real signals). It now keeps one per ticker, action and day; about 69k historical duplicates still need `scripts/dedupe_signal_outcomes.py`. | same file, docstring |
| 7.0% of resolved equity signals reached target. | `docs/mobile_trade_ideas_data_audit.md` |
| Confidence vs outcome: AUC 0.541, CI [0.498, 0.585], 9,274 signals, about 14 trading days. A calibration script shows that at this sample size an AUC of 0.55 to 0.60 is not evidence of skill. | `backend/scripts/confidence_skill_test.py`, `confidence_skill_calibration.py` |
| Options "POP" is a Black-Scholes, risk-neutral probability from the market's own implied volatility. It is not this desk's record. | `options_intelligence.py`, `options_decision_engine.py`, the audit doc above |
| The ML scorer (XGBoost, return on risk) exists, but its quality is not established by its presence; a historical input is documented as constant. | `signal_scorer.py`, `docs/audits/OLBOSTRADE_CROSS_FUNCTIONAL_AUDIT_2026-10-20.md` item 5 |

## Why win rate alone misleads

- **Geometry sets the win rate.** With a target 0.5 ATR away and a stop 3 ATR away, most trades
  "win" by construction. The losses are six times larger, so the record loses money. Lowering
  targets to reach 75% would make the app worse.
- **Credit spreads already show 70 to 80%.** Selling out-of-the-money spreads has a high
  probability of profit by design, paid for with rare, large losses. A 78% POP on a card is the
  model's view of that trade-off, not skill.
- **A base rate fools accuracy.** With a 7% hit rate, a model that always says "this will stop out"
  is 93% "accurate" and useless. That is why the repository's own test uses AUC.
- **Small samples cannot show 75%.** A measured 75% has this 95% interval (Wilson):

  | Resolved signals | Interval |
  | --- | --- |
  | 40 | 60% to 86% |
  | 100 | 66% to 83% |
  | 300 | 70% to 80% |
  | 500 | 71% to 79% |

  Signals on the same day move together, so the real intervals are wider. Each style needs a few
  hundred resolved signals, **forward-recorded**, before a claim near 75% means anything.
- **Costs matter more for day trading.** Spreads, slippage and options commissions turn small
  intraday edges negative. Results must be measured after them.

## The four styles, as they exist today

| Style | What produces signals | Measured? | Main gaps |
| --- | --- | --- | --- |
| **Swing** (days to weeks) | `equity_signal_engine.py`: rule-based daily-bar indicators, scored by `score_equity_signal()`, held up to 20 bars | Yes, by the outcome tracker | 7% target rate; confidence has no shown skill; no per-setup breakdown on screen; costs not modelled in the resolver |
| **Day trading** (intraday) | No dedicated intraday stock engine. `zero_dte.py` / `zero_dte_recorder.py` cover 0DTE options; research scripts exist (`intraday_momentum_backtest.py`, `vwap_reversion_backtest.py`, `gap_study_*`) | 0DTE is recorded; intraday stock setups are research only | The resolver walks **daily** bars, so it cannot judge an intraday signal; needs intraday bars and a time-of-day exit. Autopilot stays off (`AGENTS.md` rule 4) |
| **Options** | `options_scan_engine.py` and `options_decision_engine.py`: EV-ranked verticals with a fail-closed NO-TRADE gate | **No.** Every qualifying spread is logged (`options_signal_history.py`), but that file is "a signal log only, no resolution logic"; only trades actually taken have results | POP is risk-neutral, not empirical; EV inherits that; option-chain fetches are slow (a full pass needs about 3x the scan budget) |
| **Crypto** | `crypto_signal_engine.py`: reuses the equity scoring, read-only, no order path by design | Yes, same tracker (`asset_type`) | Same scoring as equities, so no reason yet to expect a different result; hold window counts bars (7 a week); crypto execution needs separate legal, broker and risk approval |

## The proposed quality standard

For each style, and each setup within it:

1. **Forward-recorded only.** Backtests can suggest setups; only signals recorded at generation
   time, before the outcome was known, count toward the standard.
2. **Resolved with the right bars.** Daily bars for swing and crypto; intraday bars with a
   session-end exit for day trading; option marks, or a defined payoff at expiry, for options.
3. **After costs.** A spread and slippage allowance per style, plus option commissions.
4. **Shown, not hidden.** Every published signal carries its setup's record: resolved count, win
   rate with interval, average win and loss, expectancy with interval. Expectancy is the average
   result per trade in R, the multiple of the risk taken.
5. **A publication gate.** A setup's signals are shown as "quality" only when all of these hold:
   - at least the minimum resolved count (proposed 300);
   - the expectancy interval's lower bound is above zero after costs;
   - the confidence score beats a coin flip with a date-clustered interval, or the gate ignores
     confidence;
   - and, if wanted, the win rate interval's **lower bound** is at least 75%.

   Setups that fail stay in the research view, labelled as unproven.
6. **No marketing on accuracy.** "75% accurate" is not used as a claim. A public figure, if any, is
   the live, after-cost record with its sample size and date range.

## Plan

| Phase | Work | Changes trading? |
| --- | --- | --- |
| 1. Clean the record | Run both dedupe scripts (equity and options) and add the unique indexes. Re-run the skill test and report win rate, expectancy and AUC **per style and per setup**, after costs. | No |
| 2. Show the record | A "Signal record" view: each setup's numbers and intervals, and the same record on every signal card. | No |
| 3. Measure what is unmeasured | An intraday resolver for day-trading setups; a resolver for options scan candidates; separate crypto cohorts. | No |
| 4. Gate on the record | Publish as "quality" only the setups that pass the standard; everything else is labelled research. | Only what is shown, not orders |
| 5. Improve setups | Research and retire setups using the record: fewer, better signals. Re-test the ML scorer against the same standard before it gates anything. | No; any change to routing or execution goes through the existing approvals |

## Decisions for the owner

1. Adopt the standard above instead of a flat "75% accurate" target?
2. Which publication thresholds: minimum count, a positive after-cost expectancy, and optionally a
   win-rate lower bound of 75%?
3. Is it acceptable that the gate may publish very few quality signals, or none, until the record
   supports them?
4. Day trading: stocks intraday, 0DTE options, or both? It decides which resolver is built first.
5. Crypto stays signal-only (no execution) until a separate legal and broker review?

## What this document does not decide

It does not change any engine, threshold, route, order path or setting. It makes no claim about
future results. Past results do not predict future results, and nothing here is investment advice.
