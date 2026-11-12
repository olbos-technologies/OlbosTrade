# Paper-pilot evidence worksheet

Use one private copy of this worksheet per isolated paper-account drill. This
is a **blank record**, not a completed test or permission to submit an order.
Follow `PAPER_PILOT_READINESS.md` and the applicable broker runbook. An
authorized operator conducts the drill; a separate reviewer checks the
evidence. Do not commit a filled copy to this repository.

Store raw broker records in the approved private evidence system. In shared
reports use synthetic case labels and redacted references only. Never paste
credentials, OAuth callbacks, full account identifiers, customer data, raw
order payloads, or screenshots containing them. Use UTC timestamps and record
the source of each observation.

## Scope and preflight

| Field | Operator record |
| --- | --- |
| Evidence ID / private record location | Not recorded |
| UTC start and end; operator and reviewer | Not recorded |
| Deployed git SHA and image digest; prior rollback image | Not verified |
| Migration head and policy/config version | Not verified |
| Broker, paper-account alias, and broker-side paper confirmation | Not verified |
| Application environment/account scope and execution mode | Not verified |
| Kill-switch state and unresolved order/position differences | Not verified |
| CI run URL and exact commit tested | Not verified |
| Approved order types, risk envelope, and drill stop contact | Not defined |

**Preflight decision:** NOT RUN. Stop before any order if the broker-side paper
environment, identity, kill switch, account scope, or starting positions cannot
be verified. A UI PAPER label or a health `200` is insufficient.

## Lifecycle and fault matrix

For each row, record `PASS`, `FAIL`, or `NOT RUN`; UTC time; sanitized private
evidence reference; expected and observed state; and any divergence. Do not
infer a successful fill from a request acknowledgement. Unknown broker outcome
remains `UNKNOWN` until broker reconciliation proves its terminal state.

| Case | Required observation | Result | Evidence ref / UTC time |
| --- | --- | --- | --- |
| Equity paper lifecycle | Intent → authorization/risk decision → submission → broker acknowledgement → fill/cancel → position → reconciliation | NOT RUN | — |
| Options paper lifecycle, if in pilot scope | Contract/legs/multiplier and bounded loss verified through the same lifecycle | NOT RUN | — |
| Duplicate request | One authorized effect; stable idempotency and audit linkage | NOT RUN | — |
| Timeout after broker acceptance | Ambiguous state retained; lookup/reconciliation before any retry | NOT RUN | — |
| Backend restart during lifecycle | Durable state restored; no duplicate submission or lost fill | NOT RUN | — |
| Stale or unavailable market/account data | New risk refused; missing exposure never shown as zero | NOT RUN | — |
| Broker disconnect and reconnect | New risk refused while uncertain; broker/account identity and positions reverified before resumption | NOT RUN | — |
| Partial fill, cancel, and late event | Filled quantity, remaining quantity, position and order state reconcile | NOT RUN | — |
| Kill switch | New submissions stop; authorized risk-reducing path remains separately verified | NOT RUN | — |
| Cross-account/tenant denial | Other account or tenant cannot read, approve, release or submit this pilot's intents | NOT RUN | — |
| Backup and isolated restore | Off-site copy retrieved; disposable DB restored; schema/row sample checked; broker reconciliation completed | NOT RUN | — |
| Mobile operator safety | iOS Safari and Android Chrome review for focus, zoom, screen reader, touch and unavailable states | NOT RUN | — |

For a case outside the approved pilot scope, leave `NOT RUN`, explain why, and
have the reviewer decide whether that exclusion is acceptable. Never turn a
skipped case into `PASS`. Stop the drill on unexplained order, position,
balance, protective-order, authorization, or account-scope divergence.

## Reconciliation and recovery result

| Field | Operator record |
| --- | --- |
| Broker snapshot as-of UTC and read source | Not verified |
| Application snapshot as-of UTC and read source | Not verified |
| Order, fill, position, cash and exposure differences | Not verified |
| Open ambiguous submissions or unresolved claims | Not verified |
| Backup ID, off-site retrieval, restore target isolation | Not verified |
| RPO, RTO, restore validation and post-restore broker check | Not verified |
| Rollback feasibility and observed recovery behavior | Not verified |

## Review and decision

| Field | Reviewer record |
| --- | --- |
| Failed or not-run cases and accepted scope exclusions | Not reviewed |
| Remediation owner, issue link, and repeat-drill date | Not assigned |
| Operator sign-off (name, UTC time, private evidence ref) | Not signed |
| Independent reviewer sign-off (name, UTC time) | Not signed |
| Paper-pilot decision: GO / NO-GO | NO DECISION |

Any unverified required gate means **NO-GO for expanded automation or pilot
scope**. This worksheet does not authorize live capital, customer access,
legal/vendor approval, deployment, risk-limit changes, or a mode change.
