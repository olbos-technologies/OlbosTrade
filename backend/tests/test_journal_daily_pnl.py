"""Daily realized P&L for the Journal's monthly calendar."""
from datetime import date

from app.services.journal_service import JournalAnalytics, JournalEntryOut


def _e(pnl, exit_date, entry_date=date(2026, 10, 20)):
    return JournalEntryOut(
        id="i", trade_id=None, strategy=None, underlying="SPY", pre_trade_thesis="", confidence_level=3,
        market_context="", post_trade_notes=None, followed_rules=None, exit_felt_right=None, tags=[],
        loss_category=None, mistake_tags=[], pnl=pnl, mfe=None, mae=None, signal_score=None,
        entry_date=entry_date, exit_date=exit_date,
    )


A = JournalAnalytics()


def test_days_sum_by_close_date_not_entry_date():
    rows = [_e(100.0, date(2026, 11, 3)), _e(-40.5, date(2026, 11, 3)), _e(-203.45, date(2026, 11, 1))]
    # Opened in October, closed in November: counted on the close day.
    assert A.daily_pnl(rows, "2026-11") == [
        {"date": "2026-11-01", "pnl": -203.45, "trades": 1},
        {"date": "2026-11-03", "pnl": 59.5, "trades": 2},
    ]


def test_other_months_open_and_unknown_pnl_are_left_out():
    rows = [
        _e(50.0, date(2026, 10, 31)),      # other month
        _e(25.0, None),                    # still open
        _e(None, date(2026, 11, 4)),       # closed, P&L unknown: not a zero
        _e(10.0, date(2026, 11, 4)),
    ]
    assert A.daily_pnl(rows, "2026-11") == [{"date": "2026-11-04", "pnl": 10.0, "trades": 1}]


def test_empty_month_is_an_empty_list():
    assert A.daily_pnl([], "2026-11") == []


def test_monthly_review_carries_the_daily_figures():
    rows = [_e(12.0, date(2026, 11, 2), entry_date=date(2026, 11, 1))]
    review = A.generate_monthly_review(rows, "2026-11")
    assert review.daily_pnl == [{"date": "2026-11-02", "pnl": 12.0, "trades": 1}]
