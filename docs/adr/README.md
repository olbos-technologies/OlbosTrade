# Architecture Decision Records

`MASTER_ARCHITECTURE.md` §20.2 requires an ADR before committing to a set of
named decisions. One file per decision, numbered in order, never renumbered.

A record is **Proposed** until the operator approves it, **Accepted** once they
do, and **Superseded by ADR-NNNN** if a later decision replaces it. An accepted
ADR is not edited to change its decision — a new one supersedes it, so the
reasoning that was true at the time stays readable.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-broker-connection-ownership.md) | Broker connection ownership semantics (§23 item 3) | Accepted 2026-10-05 |
| [0002](0002-legal-operating-model.md) | Legal operating model and regulated scope (§23 item 1) | Open — awaiting counsel |

A record may also be **Open**, meaning the decision is not engineering's to
make and is waiting on someone outside the team. An Open ADR states the
question and the facts needed to answer it, and proposes nothing.

Still required by §20.2, not yet written: identity provider versus the existing
session service; managed cloud and
vault/KMS; durable queue technology; event schema and retention; OAuth versus a
temporary Alpaca API-key bridge; IBKR integration model; market-data vendors and
licenses; HA/DR region strategy; audit immutability method; live SLOs and
support coverage.
