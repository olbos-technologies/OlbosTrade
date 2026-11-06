## Unreleased — Options chain retrieval and research quality

- Read paginated Alpaca option contracts and snapshots in batches of at most 100 symbols, with bounded collection time and no partial-success response after a failed page.
- Add experimental two-leg liquidity and strategy delta-fit evidence; keep EV primary in research results and retain the existing automated candidate ranker and execution gates.
- Preserve quote source time and explicit feed provenance; unknown or stale quotes receive no numeric quality score.
- Record previously silent regime/portfolio-state scan blocks and worker failures; make empty rejection reports explicitly inconclusive.
