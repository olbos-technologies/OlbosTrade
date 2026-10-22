"""R2-A: conservative contract-level comparison primitive.

Not wired to order submission yet. Unknown spread structures remain unverified,
not clean, until their leg mapping is explicitly supported and tested.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
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
            if not leg.underlying or leg.option_type.strip().lower() not in ("call", "put") or leg.quantity == 0:
                return ContractComparison(verified=False, clean=False, reasons=["invalid_contract_leg"])
            source[leg.key] += int(leg.quantity)
    keys = set(broker) | set(recorded)
    missing_at_db = {key: broker[key] - recorded[key] for key in keys if broker[key] > recorded[key]}
    missing_at_broker = {key: recorded[key] - broker[key] for key in keys if recorded[key] > broker[key]}
    return ContractComparison(
        verified=True,
        clean=not missing_at_db and not missing_at_broker,
        broker_only=missing_at_db,
        db_only=missing_at_broker,
    )
