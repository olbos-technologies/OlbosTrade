# R2 history import staging

This is an offline, single-writer quarantine component, not the PostgreSQL
execution ledger, authenticated broker history provider, or order authority.
`HistoryImportStaging` accepts normalized records from a future download/parser
adapter. It does **not** accept native Flex XML/CSV yet. No endpoint, scheduler,
credential, network request, broker order, or execution-path integration exists.

## Protocol

1. Create a staging SQLite file inside a private directory owned by the
   operator. It contains account identifiers and report records; never commit
   or share it, and never place it in the web/static tree. This prototype does
   not encrypt the file or manage retention; use synthetic data for now.
2. `begin(import_id, account=..., organization=..., start=..., end=..., pages=N)`
   records a declared half-open UTC/offset-aware period `[start,end)` and page
   count. Reusing an ID with a different manifest is rejected. The organization
   is a nonempty staging label; it is not authenticated tenant identity.
3. `add_page(import_id, page_number, records)` accepts dictionaries containing
   `execution_id`, `account`, `organization`, and offset-aware ISO `filled_at`,
   plus adapter-defined payload. This layer validates scope/time/identity, not
   financial contract fields; existing fill validation must run before any
   later ledger promotion. The declared manifest is not completeness proof.
4. Identical page replay is idempotent while collecting. Duplicate executions
   across pages are stored once. Changed page content or a changed execution
   payload quarantines the import. Corrections require review/new evidence;
   old executions are never overwritten. All rows and the receipt for a page
   commit together; rejected page writes roll back.
5. Missing pages prevent finalization. A restart can resume the same file.
   Finalization returns `staged_unverified`, **always verified=false**. Even
   an empty complete page set is unverified. Quarantined imports cannot finish.

Use one importer per file; concurrent workers/finalization are not certified.
Rows are deduplicated within each import only; cross-import promotion must use
PostgreSQL's existing broker/account/execution identity and conflict checks.
A time span/page count cannot prove the absence of unreported executions,
orders, transfers, adjustments, or corrections.

## Fixture verification

`cd backend && python -m pytest tests/test_history_import_staging.py
--cov=app.services.history_import_staging --cov-fail-under=95`

18 tests passed, zero skips, 100% statement/branch coverage. Full-suite/CI
results belong in the PR. Fixtures use synthetic scope and execution records.
No real account credentials are needed or used.

## Next integration gates

IBKR documents customizable Flex queries and a SendRequest/GetStatement flow:
- https://www.interactivebrokers.com/docs/web-api/flex-web-service/using-flex-web-service/generate-the-report
- https://www.interactivebrokers.com/docs/web-api/api-reference/get-statement

Before real imports: define and version the exact report template and native
parser, prove broker/account ownership through a trusted server-side source,
validate retrieval errors/retries, period/section completeness and correction
semantics, protect credentials and staging files, then transactionally promote
validated evidence with a durable coverage record. A fixture, exported file,
client-supplied boolean, or IBKR session cache cannot establish that trust.
Order-history and full-account position coverage remain separate requirements;
this execution-record quarantine cannot authorize trading.

## Handoff

Milestone R2 history ingestion staging. Base
`df31690f1fce3a65effe7d408925f659c7165554`; branch `codex/r2-history-staging`.
Files: staging service, tests, this guide, roadmap and changelog. No PostgreSQL
migration, new environment variable, frontend edit or deployment. No broker
orders. Rollback removes an unused prototype; existing execution is unchanged.
Next owner completes native parser/authenticated transport and promotion gates;
PR #149 remains separate WIP.
