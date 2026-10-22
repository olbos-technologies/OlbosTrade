"""R2-C conservative inference of *candidate* option legs from trade rows.

This is NOT broker fill evidence. Inferred strategy legs cannot authorize new
orders until matched to immutable executed fill records, including quantities,
partials, per-account ownership and any adjustments since entry.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.contract_reconciliation import ContractLeg


@dataclass(frozen=True)
class LegMapping:
    verified: bool
    legs: tuple[ContractLeg, ...]
    reason: str | None = None


# Deliberately limited to strategies whose short/long leg kinds are explicit.
_KNOWN_TWO_LEG_KINDS = {
    "bull_put_spread": "put",
    "bear_call_spread": "call",
    "bull_call_debit_spread": "call",
    "bear_put_debit_spread": "put",
}


def infer_candidate_legs(trade: Any) -> LegMapping:
    """Return unverified candidate legs for supported *standard* spreads.

    Never turn candidate legs into a clean authorization decision: the DB trade
    row stores intended strategy-level contracts, not actual broker fill legs.
    """
    strategy = str(getattr(trade, "spread_type", "") or "").lower()
    option_type = _KNOWN_TWO_LEG_KINDS.get(strategy)
    if option_type is None:
        return LegMapping(False, (), "unsupported_or_ambiguous_strategy")
    try:
        qty = getattr(trade, "quantity")
        qty = int(qty) if qty is not None else 0
        short = Decimal(str(getattr(trade, "short_strike")))
        long = Decimal(str(getattr(trade, "long_strike")))
        expiry = getattr(trade, "expiration")
        underlying = str(getattr(trade, "underlying", "")).strip().upper()
        if qty <= 0 or short <= 0 or long <= 0 or short == long or not underlying or expiry is None:
            return LegMapping(False, (), "invalid_or_missing_spread_fields")
        legs = (
            ContractLeg(underlying, expiry, short, option_type, -qty),
            ContractLeg(underlying, expiry, long, option_type, qty),
        )
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return LegMapping(False, (), "invalid_or_missing_spread_fields")
    return LegMapping(False, legs, "strategy_intent_is_not_fill_evidence")
