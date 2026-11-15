# Zomi glossary: terms waiting for a reviewed translation

Only terms a Zomi speaker has reviewed go in `frontend/src/i18n/zo.ts`. Until then
the interface shows the English text, which is safe and never blank.

## Reviewed and live

| English | Zomi |
| --- | --- |
| Home | Inn |
| Markets | Market |
| Sign out | Pusuak |

## Needed (navigation and general)

Positions, Orders, Signals, Portfolio, Account, Journal, Command Center, Trade
Desk, Brokers, Performance, Research, Charts, Refresh, Settings, Help, Search,
Language.

## Needed and safety-sensitive: exact sentence approval required

These are blocked by the guard test until approved and added to
`ZO_SAFETY_REVIEWED`. Please review them as full sentences, not single words.

Kill switch, Autopilot, Copilot, Manual, Place order, Confirm order, Daily loss
limit, Risk limits, Stop loss, Paper trading, Live trading, Not financial advice,
and every disclosure paragraph.

## How to contribute a term

Give the exact English string as it appears in the app and the Zomi you would use,
with a note on the register (formal or everyday). Please say if a word is
borrowed from English on purpose, as "Market" is above.
