# Plan benchmark: our tiers against TradingView's

**Status:** proposal for discussion. Nothing here changes a limit, a price or any code.
Tier limits are enforced by the backend (`backend/app/services/tier_limits.py`) and checked
against the landing page by a test, so any change is a deliberate, owner-approved edit.

## Sources, and how far to trust them

- **Olbos Trade / Cimphawng Trading:** read from the code on `main` (`tier_limits.py`,
  `Landing.tsx` `PLANS`). Both brands share one set of tiers.
- **TradingView:** the figures below are **a summary the owner pasted** on 2026-11-14, said
  to come from TradingView's pricing page. I have not checked them against the page and
  prices change. Treat them as indicative; re-check before relying on them.

## Side by side

| | **Free** | **Pro** | **Elite** |
| --- | --- | --- | --- |
| Our price (billing is **not** wired; intended) | $0 | $29/mo | $99/mo |
| Watchlist | 1 ticker | full | full |
| Signal delay | end of day | live | live |
| History | 1 year | 5 years | 5 years |
| Broker connection (Copilot execution) | no | no | yes |

| | **Essential** | **Plus** | **Premium** | **Ultimate** |
| --- | --- | --- | --- | --- |
| TradingView price (monthly, as pasted) | $14.95 | $34.95 | $69.95 | $239.95 |
| Charts per tab | 2 | 4 | 8 | 16 |
| Indicators per chart | 5 | 10 | 25 | 50 |
| Historical bars | 10K | 10K | 20K | 40K |
| Active price alerts | 20 | 100 | 400 | 1,000 |

## What the comparison shows

1. **They are not selling the same thing.** TradingView prices charting capacity. We price
   access to signals, a fail-closed risk gate and broker-connected Copilot execution. Our
   tiers differ on data freshness and execution access; theirs differ on quotas.
2. **Price points overlap loosely.** Pro ($29) sits between their Essential and Plus; Elite
   ($99) sits between Premium and Ultimate. That says little, because the products differ.
3. **We have no charting quotas to sell.** The workspace shows **one** chart. The indicators
   (MA 5/10/20, VWAP, MACD, volume, volume profile) are a fixed set, not a user-chosen list.
   Alert rules exist but are only rate-limited, not capped per tier. History is already tiered
   (1 / 5 / 5 years). **Selling a limit for a feature that does not exist yet would be a
   mistake**, so charts per tab and indicators per chart cannot be tiers until a multi-chart
   view and an indicator picker exist.
4. **Volume profile is not gated.** TradingView includes it on every paid plan. Ours is in
   the workspace for every tier today. That is a choice to make, not an accident to fix.
5. **Real-time data has a cost we do not control.** Their top plan advertises unlimited
   real-time subscriptions for professional users. Our Free tier is end-of-day by design.
   Any real-time promise depends on market-data rights we have not confirmed.

## Options

| | What | Cost and risk |
| --- | --- | --- |
| **A. Change nothing** (recommended now) | Keep Free / Pro / Elite as published. | None. Billing is not wired, so prices are still intended, not charged. |
| **B. Cap alert rules per tier** | e.g. a count per tier. Alert rules already exist, so this is a server-side count at creation. | Small and enforceable. Needs your numbers, a landing-page row and a test update. Existing users above a new cap need a rule (grandfather or warn). |
| **C. Gate the volume profile** | Pro and Elite only. | Easy, but takes a visible feature away from Free users, and the workspace is new. |
| **D. Charts per tab / indicators per chart** | Tier the multi-chart and indicator features. | **Not possible yet.** Build the features first, then decide tiers. |

## Decisions for the owner

1. Is the positioning "signals plus execution", not "charting", the one to keep? If yes, option A
   is the default and charting quotas are a later extra, not a core tier driver.
2. If you want a charting-capacity tier later, which feature comes first: a multi-chart view or
   an indicator picker? That choice sets what can be tiered.
3. Prices are unchanged and uncharged until billing exists. When it does, check the competitor
   figures again and settle the market-data rights for any real-time claim.
