"""Side-effect-free evaluation of already-fetched margin data.

This is a preparation check, not an authorization or a broker fetch.
A missing or stale account snapshot never provides affirmative margin clearance.
"""


def evaluate_entry_margin_snapshot(account, *, evaluate_margin, warn_pct, critical_pct):
    """Return (block_reason, stale_age) without touching broker or persistence.

    block_reason is only populated when fresh data positively proves critical
    margin; all other cases defer to independent, mandatory entry checks.
    """
    if account.is_stale:
        return None, account.data_age_seconds or 0.0
    if account.maintenance_margin is None:
        return None, None
    result = evaluate_margin(
        net_liquidation=float(account.net_liquidation or 0),
        maintenance_margin=float(account.maintenance_margin or 0),
        excess_liquidity=float(account.excess_liquidity or 0),
        buying_power=float(account.buying_power or 0),
        init_margin=float(account.init_margin or 0),
        warn_pct=warn_pct,
        critical_pct=critical_pct,
    )
    if result.status == "critical":
        return f"margin_critical: {result.detail}", None
    return None, None
