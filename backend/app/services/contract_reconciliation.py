"""R2-A: conservative contract-level comparison primitive.

Not wired to order submission yet. Unknown spread structures remain unverified,
not clean, until their leg mapping is explicitly supported and tested.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class ContractLeg:
    underlying: str
    expiration: date
    strike: Decimal
    option_type: str
    quantity: int  # signed: long positive, short negative

    @property
    def key(self) -> tuple:
        return (
            self.underlying.strip().upper(),
            self.expiration,
            Decimal(str(self.strike)),
            self.option_type.strip().lower(),
        )


@dataclass
class ContractComparison:
    verified: bool
    clean: bool
    broker_only: dict[tuple, int] = field(default_factory=dict)
    db_only: dict[tuple, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def compare_contract_legs(
    broker_legs: Iterable[ContractLeg],
    recorded_legs: Iterable[ContractLeg] | None,
) -> ContractComparison:
    """Require complete DB leg evidence and signed contract-level equality.

    None means the database representation cannot be established; unlike [],
    it must never be interpreted as an empty portfolio.
    """
    if recorded_legs is None:
        return ContractComparison(verified=False, clean=False, reasons=["db_contract_legs_unavailable"])
    broker = Counter()
    recorded = Counter()
    for source, legs in ((broker, broker_legs), (recorded, recorded_legs)):
        for leg in legs:
            # Type annotations do not validate broker/database evidence. Never
            # truncate a fractional quantity or accept a non-finite identity.
            try:
                strike = Decimal(str(leg.strike))
                valid = (
                    isinstance(leg.underlying, str) and bool(leg.underlying.strip())
                    and type(leg.expiration) is date
                    and isinstance(leg.option_type, str)
                    and leg.option_type.strip().lower() in ("call", "put")
                    and type(leg.quantity) is int and leg.quantity != 0
                    and strike.is_finite() and strike > 0
                )
            except (InvalidOperation, ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                return ContractComparison(verified=False, clean=False, reasons=["invalid_contract_leg"])
            source[leg.key] += leg.quantity
    keys = set(broker) | set(recorded)
    # Report mismatched signed holdings by *source*, not numeric comparison.
    # A short broker position (-1) absent from DB (0) is broker-only;
    # comparing -1 > 0 incorrectly inverted it. Preserve both sides when
    # contract direction or size differs, so the operator sees actual holdings.
    mismatch = {key for key in keys if broker[key] != recorded[key]}
    missing_at_db = {key: broker[key] for key in mismatch if broker[key] != 0}
    missing_at_broker = {key: recorded[key] for key in mismatch if recorded[key] != 0}
    return ContractComparison(
        verified=True,
        clean=not missing_at_db and not missing_at_broker,
        broker_only=missing_at_db,
        db_only=missing_at_broker,
    )
