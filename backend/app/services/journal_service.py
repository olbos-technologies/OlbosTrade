"""
Trading journal service — full CRUD + analytics.
Auto-populates trade fields from the trades table on entry creation.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class JournalEntryCreate:
    """Data needed to create a new journal entry (pre-trade)."""
    trade_id: Optional[str]
    pre_trade_thesis: str
    confidence_level: int               # 1–5
    market_context: str
    strategy: Optional[str] = None
    underlying: Optional[str] = None


@dataclass
class JournalEntryUpdate:
    """Post-trade fields added after closing a position."""
    post_trade_notes: str
    followed_rules: bool
    exit_felt_right: bool
    tags: list[str]
    loss_category: Optional[str] = None
    mistake_tags: Optional[list[str]] = None


@dataclass
class JournalEntryOut:
    """Full journal entry for API responses."""
    id: str
    trade_id: Optional[str]
    strategy: Optional[str]
    underlying: Optional[str]
    pre_trade_thesis: str
    confidence_level: int
    market_context: str
    post_trade_notes: Optional[str]
    followed_rules: Optional[bool]
    exit_felt_right: Optional[bool]
    tags: list[str]
    loss_category: Optional[str]
    mistake_tags: list[str]
    pnl: Optional[float]
    mfe: Optional[float]
    mae: Optional[float]
    signal_score: Optional[float]
    entry_date: Optional[date]
    exit_date: Optional[date]


@dataclass
class TagPerformance:
    """Win rate and avg P&L grouped by a journal tag."""
    tag: str
    trade_count: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


@dataclass
class RuleBreachImpact:
    """The headline P&L impact of following vs breaching rules."""
    followed_avg_pnl: float
    breached_avg_pnl: float
    followed_win_rate: float
    breached_win_rate: float
    followed_count: int
    breached_count: int
    pnl_delta: float        # followed_avg - breached_avg
    summary: str            # e.g. "When rules followed: +$120 | When breached: -$340"


@dataclass
class MonthlyReview:
    """Auto-generated monthly review summary."""
    month: str              # "2026-01"
    total_trades: int
    win_rate: float
    total_pnl: float
    rules_followed_pct: float
    top_tags: list[str]
    top_mistakes: list[str]
    mistake_frequency: dict[str, int]
    confidence_vs_outcome: list[dict]
    recommendation: str
    # Realized P&L per calendar day, by the day the trade CLOSED: [{date, pnl, trades}].
    # Separate from total_pnl above, which groups by entry month.
    daily_pnl: list[dict] = field(default_factory=list)


class JournalAnalytics:
    """
    Analytics engine over journal entries.
    Takes raw entry + trade data and computes all analytics views.
    """

    def tag_performance(self, entries: list[JournalEntryOut]) -> list[TagPerformance]:
        """Aggregate win rate and avg P&L by tag."""
        tag_data: dict[str, list[float]] = {}
        for entry in entries:
            for tag in entry.tags:
                tag_data.setdefault(tag, [])
                if entry.pnl is not None:
                    tag_data[tag].append(entry.pnl)

        results = []
        for tag, pnls in tag_data.items():
            if not pnls:
                continue
            wins = [p for p in pnls if p > 0]
            results.append(TagPerformance(
                tag=tag,
                trade_count=len(pnls),
                win_rate=len(wins) / len(pnls),
                avg_pnl=sum(pnls) / len(pnls),
                total_pnl=sum(pnls),
            ))
        return sorted(results, key=lambda x: x.avg_pnl, reverse=True)

    def mistake_frequency(self, entries: list[JournalEntryOut]) -> dict[str, int]:
        """Count occurrences of each mistake tag."""
        counts: dict[str, int] = {}
        for entry in entries:
            for mistake in entry.mistake_tags:
                counts[mistake] = counts.get(mistake, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def rules_followed_pct(self, entries: list[JournalEntryOut]) -> float:
        """Percentage of trades where rules were followed."""
        with_data = [e for e in entries if e.followed_rules is not None]
        if not with_data:
            return 0.0
        followed = sum(1 for e in with_data if e.followed_rules)
        return round(followed / len(with_data) * 100, 1)

    def rule_breach_impact(self, entries: list[JournalEntryOut]) -> RuleBreachImpact:
        """
        The most important analytics card: what did rule-following cost/earn?
        """
        followed = [e for e in entries if e.followed_rules is True and e.pnl is not None]
        breached = [e for e in entries if e.followed_rules is False and e.pnl is not None]

        def stats(group: list[JournalEntryOut]) -> tuple[float, float]:
            if not group:
                return 0.0, 0.0
            pnls = [e.pnl for e in group]
            avg = sum(pnls) / len(pnls)
            wins = sum(1 for p in pnls if p > 0) / len(pnls)
            return avg, wins

        followed_avg, followed_wr = stats(followed)
        breached_avg, breached_wr = stats(breached)

        summary = (
            f"When rules followed: {'+' if followed_avg >= 0 else ''}${followed_avg:.0f} avg | "
            f"When breached: {'+' if breached_avg >= 0 else ''}${breached_avg:.0f} avg"
        )

        return RuleBreachImpact(
            followed_avg_pnl=round(followed_avg, 2),
            breached_avg_pnl=round(breached_avg, 2),
            followed_win_rate=round(followed_wr, 3),
            breached_win_rate=round(breached_wr, 3),
            followed_count=len(followed),
            breached_count=len(breached),
            pnl_delta=round(followed_avg - breached_avg, 2),
            summary=summary,
        )

    def confidence_vs_outcome(self, entries: list[JournalEntryOut]) -> list[dict]:
        """Scatter data: confidence level (1–5) vs actual P&L."""
        return [
            {"confidence": e.confidence_level, "pnl": e.pnl, "followed_rules": e.followed_rules}
            for e in entries
            if e.confidence_level is not None and e.pnl is not None
        ]

    def daily_pnl(self, entries: list[JournalEntryOut], month: str) -> list[dict]:
        """Realized P&L per day for `month` ("YYYY-MM"), by close date.

        Only closed trades with a known P&L count; a trade with unknown P&L is left
        out rather than counted as zero, so a day never shows a made-up figure.
        """
        days: dict[str, list[float]] = {}
        for e in entries:
            if e.exit_date is None or e.pnl is None:
                continue
            if e.exit_date.strftime("%Y-%m") != month:
                continue
            days.setdefault(e.exit_date.isoformat(), []).append(e.pnl)
        return [
            {"date": d, "pnl": round(sum(p), 2), "trades": len(p)}
            for d, p in sorted(days.items())
        ]

    def generate_monthly_review(
        self, entries: list[JournalEntryOut], month: str
    ) -> MonthlyReview:
        """
        Auto-generate a monthly review summary.
        month: "YYYY-MM"
        """
        month_entries = [
            e for e in entries
            if e.entry_date and e.entry_date.strftime("%Y-%m") == month
        ]

        pnls = [e.pnl for e in month_entries if e.pnl is not None]
        total_pnl = sum(pnls) if pnls else 0.0
        win_rate = len([p for p in pnls if p > 0]) / len(pnls) if pnls else 0.0
        rules_pct = self.rules_followed_pct(month_entries)
        mistakes = self.mistake_frequency(month_entries)

        # Top tags by frequency
        tag_counts: dict[str, int] = {}
        for e in month_entries:
            for tag in e.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tags = sorted(tag_counts, key=lambda t: -tag_counts[t])[:5]

        top_mistakes = list(mistakes.keys())[:3]

        # Recommendation
        if rules_pct < 70:
            rec = "Focus on rule adherence — you violated rules in over 30% of trades this month."
        elif win_rate < 0.50:
            rec = "Win rate below 50%. Review entry conditions and consider tightening signal threshold."
        elif total_pnl > 0 and win_rate > 0.60:
            rec = "Strong month. Maintain current discipline and consider gradual position size increase."
        else:
            rec = "Continue following the system. Paper trade results take time to reflect edge."

        return MonthlyReview(
            month=month,
            total_trades=len(month_entries),
            win_rate=round(win_rate, 3),
            total_pnl=round(total_pnl, 2),
            rules_followed_pct=rules_pct,
            top_tags=top_tags,
            top_mistakes=top_mistakes,
            mistake_frequency=mistakes,
            confidence_vs_outcome=self.confidence_vs_outcome(month_entries),
            recommendation=rec,
            daily_pnl=self.daily_pnl(entries, month),
        )
