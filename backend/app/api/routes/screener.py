"""
Read-only S&P 500 daily screener.

GET /api/screener/sp500 returns the cached snapshot at once and starts a
background rebuild when it is older than 15 minutes (see
app/services/screener/snapshot.py). Market data only: no account, no orders.
"""

from fastapi import APIRouter

from app.services.screener.snapshot import current_view

router = APIRouter()


@router.get("/sp500")
async def get_sp500_screener():
    return current_view()
