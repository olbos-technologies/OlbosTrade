"""Broker adapters for resolving unresolved position claims.

`position_claim.reconcile_unresolved` needs an answer to one question: does the
broker hold an order carrying this claim's idempotency key? This module turns
a broker into something that can answer it, and — more importantly — makes
sure it says "I cannot tell" whenever that is the truth.

Three ways to get that wrong, all of which release a claim guarding a live
position:

1. **Unsupported lookup.** A broker with no client-id index cannot establish
   absence. `BrokerInterface.find_order_by_client_order_id` defaults to
   UNDETERMINED, so this is the behaviour a broker gets by doing nothing;
   declaring otherwise requires overriding the method. IBKR does not, so IBKR
   claims are never released automatically.

2. **Delayed visibility.** An accepted order can read as missing for a short
   while. The adapter cannot see that, so it is handled above it by
   `reconcile_unresolved`'s settle window — and the settle window is NOT on
   its own evidence of anything (see the warning below).

3. **A key the broker never received.** This is the subtle one. Only the
   options path attaches the claim's key as `client_order_id`;
   `place_equity_order` takes no such parameter. So for an equity claim,
   Alpaca's 404 means "nothing was ever tagged with this id" — which is true
   and tells us nothing about whether an equity order exists. Reading it as
   absence would release the claim on exactly the position with no broker-side
   dedup behind it. Equity claims therefore report UNDETERMINED.

**Waiting is not evidence.** Passing the settle window only makes an
authoritative NOT_FOUND believable; it never turns silence, an unsupported
lookup or an untransmitted key into absence. A claim nothing can speak to
stays blocked until an operator releases it through the audited override.
"""

from __future__ import annotations

from typing import Optional

from app.broker.broker_interface import OrderLookup
from app.services.position_claim import BrokerVerdict
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Asset classes whose submission path actually transmits the claim's
#: idempotency key to the broker. This is a property of OUR code, not the
#: broker's: trade_desk sets `client_order_id` on the options order and
#: `place_equity_order` accepts no equivalent. A claim outside this set can
#: never be resolved by a client-id lookup, however capable the broker is.
#:
#: `test_equity_orders_still_carry_no_client_order_id` fails if the equity
#: path gains one, so this set cannot quietly fall out of date.
KEYED_ASSET_CLASSES = frozenset({"options"})

_VERDICT = {
    OrderLookup.FOUND: BrokerVerdict.PRESENT,
    OrderLookup.NOT_FOUND: BrokerVerdict.ABSENT,
    OrderLookup.UNDETERMINED: BrokerVerdict.INDETERMINATE,
}


def lookup_for(broker: Optional[object]):
    """Build the `BrokerLookup` that reconciliation should use for `broker`.

    Returns a callable taking a claim row and returning a `BrokerVerdict`. With
    no broker at all it returns one that answers INDETERMINATE for everything,
    so a reconciliation sweep with no broker configured is a no-op rather than
    a mass release.
    """

    async def _lookup(claim) -> BrokerVerdict:
        if broker is None:
            logger.warning(
                "No broker available to resolve claim %s (%s %s) — undetermined",
                claim.claim_token, claim.underlying, claim.asset_class,
            )
            return BrokerVerdict.INDETERMINATE

        if claim.asset_class not in KEYED_ASSET_CLASSES:
            # Not a failure, and worth saying plainly: this claim is not
            # resolvable by lookup at all, and no amount of waiting changes it.
            logger.info(
                "Claim %s is %s, whose orders carry no client_order_id — the "
                "broker cannot be asked about key %s; undetermined",
                claim.claim_token, claim.asset_class, claim.idempotency_key,
            )
            return BrokerVerdict.INDETERMINATE

        result = await broker.find_order_by_client_order_id(claim.idempotency_key)
        verdict = _VERDICT.get(result, BrokerVerdict.INDETERMINATE)
        if verdict is BrokerVerdict.INDETERMINATE:
            logger.warning(
                "Broker could not establish the state of claim %s (key %s, "
                "returned %s) — leaving it blocked",
                claim.claim_token, claim.idempotency_key, result,
            )
        return verdict

    return _lookup
