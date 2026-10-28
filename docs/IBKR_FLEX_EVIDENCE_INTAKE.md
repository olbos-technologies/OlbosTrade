# R2: native IBKR Flex execution evidence intake

Status: **offline, read-only candidate intake**. No broker transport, credentials,
endpoint, scheduler, ledger promotion, reconciliation proof or order authority.
The code runs without the API server or a broker connection.

This supplies the native XML parser missing from the separate draft history
staging work (PR #157). It is based on main and does not merge or overwrite that
branch, PR #149, or the UI work. `staging_record()` emits dictionaries compatible
with the draft staging protocol; no automatic storage/promotion is connected.

## Supported input, version 1

- UTF-8 **Activity Flex XML**, root `FlexQueryResponse type="AF"`.
- Exactly one `FlexStatements` container with `count="1"`, one `FlexStatement`,
  and one declared account. Account matching is exact; no account prefix is
  treated as proof of paper mode, broker ownership, or tenant authorization.
- `fromDate` and `toDate` in `yyyyMMdd`. Both dates are inclusive in the input;
  the candidate period becomes a half-open UTC interval `[start,end)`.
- One `Trades` section, with only `Trade levelOfDetail="EXECUTION"` rows.
  A missing Trades section is an error, including a positions/P&L-only report.
  An explicitly empty Trades section parses but never proves no activity.
- Stocks (`assetCategory="STK"`) and individual listed option execution rows
  (`OPT`). Other asset classes, summary/order/closed-lot rows, and mixed reports
  are rejected rather than silently excluded. A stock multiplier must be 1;
  options preserve the reported positive multiplier and integral contract count.
- Required row fields: `accountId`, `levelOfDetail`, `ibExecID`, `ibOrderID`,
  `assetCategory`, `conid`, `symbol`, `quantity`, `tradePrice`, `multiplier`,
  `buySell`, `currency`, `dateTime`, `ibCommission`, `ibCommissionCurrency`, and
  `transactionType` (`ExchTrade` or `Trade`). Options also require
  `underlyingSymbol`, `expiry`, `strike`, `putCall` (`P`/`C`).
- Date/time format: `yyyyMMdd;HHmmss`. The caller must supply the **confirmed
  report timezone** as an IANA name. Never infer it from the browser/computer,
  infer midnight from a trade date, or guess a DST offset. Repeated/nonexistent
  local times are rejected. An unknown timezone remains a blocker.
- Decimal values are retained as decimal strings in staging records, not
  floating-point approximations. SELL quantities become negative; fractional
  stock quantities remain supported. Contract identity, option economics,
  currency, commission and commission currency remain separate fields.
- Limit: 10 MiB and 25,000 trade rows per file. DTDs/entities, non-UTF-8 input,
  unsupported document shapes and malformed fields are rejected. CLI reads are
  bounded too. No XML-related network/file lookup is performed.

IBKR lets a user customize sections, detail levels and fields. Therefore this
parser supports the explicit subset above, **not every Flex template**. Tests
use synthetic exports; validation against a private real export is still needed.

## Cancellation, corrections and duplicates

Include the report's correction/original-trade fields and Notes/Codes when
exporting, and include offsetting trade/cancel pairs for later coverage review.
An omitted column or query filter cannot be detected from an ordinary trade row;
there is no completeness claim even if all received rows parse.

Nonempty original-trade/order/transaction/date references, nonzero original
trade price or changes, `Ca`/`Co` notes, known revised four-component execution
IDs, and conflicting repeated IDs reject the entire file. Ordinary `C` means a
closing trade and is not confused with `Ca` cancellation. Identical repeated
source rows are counted once. Hashes cover original row attributes, so even
changed source fields that are not used in position arithmetic require review.

Execution-ID families with different trailing components are conservatively
rejected for manual review. This can also reject legitimate inner-combo fills;
**combo/revision-family resolution is not implemented**. No correction replaces
or deletes an existing ledger event. Detection is limited to markers present in
the received file; it does not establish complete broker correction history.

## Private offline inspection

From the repository root, with Python 3.11+ and timezone data installed:

```bash
python backend/scripts/inspect_ibkr_flex.py \
  --file /private/path/report.xml \
  --expected-account SYNTHETIC_ACCOUNT \
  --organization 00000000-0000-4000-8000-000000000001 \
  --timezone America/New_York
```

Replace the synthetic scope with the intended **declared** import scope and
confirm the timezone. These arguments are not authenticated ownership. Do not
commit real reports, put them in a public/static folder, share account IDs or
logs, or pass any broker token. This command neither needs nor accepts a token.

Exit 0 prints `parsed_unverified`, declared UTC period, report hash, counts and
asset classes. Every success explicitly retains:

```json
{"verified": false, "source_authenticated": false,
 "history_coverage_verified": false, "entry_authorized": false}
```

Exit 2 prints a sanitized rejection reason and optional row number. Raw XML,
account/tenant IDs, symbols, execution/order IDs and parser exception details
are not printed. No file or database is written, and no HTTP request is made.
The lower-level candidate/staging records contain private fields and must stay
inside a protected import workflow.

## What still blocks authenticated broker evidence

1. A trusted, authenticated retrieval path and independently verified
   account/organization ownership. An uploaded file and user-selected scope are
   not that evidence.
2. Verified query configuration, retrieval status, coverage interval and all
   pages/sections, with correction and combo semantics fully resolved.
3. Durable, transactional, account/tenant-scoped ledger ingestion and a coverage
   record; the draft SQLite staging store is not this production ledger.
4. Independent fresh positions, working orders/protective stops and balances.
   An execution report alone cannot prove the current book, cash or stop cover.
5. Reconciliation and one-use authorization at every actual submit path, with
   restart/retry/race and independently authorized exit tests.
6. Isolated paper-broker fault drill and operator/reviewer evidence.

No successful parser result may be converted into `verified=True` or used to
unblock new entries. Existing submission policies and close/kill paths are
unchanged by this unused read-only intake.

## Primary references

- [IBKR Trades / Activity Flex fields and detail levels](https://www.ibkrguides.com/reportingreference/reportguide/tradesfq.htm)
- [IBKR Activity Statement codes](https://www.ibkrguides.com/reportingreference/reportguide/codes_default.htm)
- [IBKR execution IDs, corrections and Gateway history limits](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details)

## Verification and handoff

Milestone R2 native evidence intake. Base main
`df31690f1fce3a65effe7d408925f659c7165554`; branch
`codex/r2-flex-evidence-intake`. Files: intake service, offline inspector, tests,
this guide, CI step, roadmap and changelog. No schema/migration, production
configuration, UI edit or broker call. Required CI job names remain unchanged.
New parser and CLI are held to 95% statement/branch coverage in the existing
backend job. Full CI and PostgreSQL evidence are recorded in the PR.

Rollback removes an unused intake tool; no active order-routing behavior changes.
Next owner: validate a private broker export, then implement authenticated
transport and durable scoped promotion before integration with entry authority.
