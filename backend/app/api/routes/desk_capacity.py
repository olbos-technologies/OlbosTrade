"""GET /api/trade-desk/capacity - why the desk may hold N positions, read-only.

Reads the same rule the portfolio gate enforces and the same open-position count
it uses (the larger of the DB's open trades and the broker's live positions), and
the last day's blocked entries. Places, changes and writes nothing. If the open
count cannot be read it says so (null) instead of showing 0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/trade-desk/capacity", tags=["trade-desk"])


async def _recent_block_rows(hours: int = 24) -> list[tuple]:
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ExecutionEvent.status, ExecutionEvent.payload)
            .where(ExecutionEvent.kind == "execution",
                   ExecutionEvent.status.in_(("blocked", "skipped")),
                   ExecutionEvent.created_at >= since)
            .limit(5000)
        )).all()
    return [(r[0], r[1]) for r in rows]


@router.get("")
async def capacity() -> dict:
    from app import main as main_mod
    from app.services.desk_capacity import describe_capacity, top_block_reasons
    from app.services.execution_portfolio_gate import load_portfolio_risk_state
    from app.services.trading_mode import trading_mode_manager

    open_positions = None
    try:
        state = await load_portfolio_risk_state(0.0)
        open_positions = int(state.open_position_count)
    except Exception as exc:                      # unreadable is not zero
        logger.warning("capacity: open positions unavailable: %s", exc)

    blocks_ok = True
    try:
        blocks = top_block_reasons(await _recent_block_rows())
    except Exception as exc:
        blocks_ok = False
        blocks = []
        logger.warning("capacity: recent blocks unavailable: %s", exc)

    cfg = trading_mode_manager.config
    out = describe_capacity(
        global_cap=int(settings.max_concurrent_positions),
        style=cfg.mode.value, style_cap=int(cfg.max_concurrent),
        regime=main_mod._regime_name(), open_positions=open_positions, blocks=blocks,
    )
    out["blocks_available"] = blocks_ok
    out["blocks_window_hours"] = 24
    return out
