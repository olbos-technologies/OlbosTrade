"""Bucketing and verdict logic for the options funnel (pure; the SQL is in test_options_funnel_pg.py).

The reason strings below are the ones the code actually writes: the scan's
`_record_options_rejection(...)` calls, each strategy's `generate_signal` reasons, and
`_execute_signal`'s `_blocked(...)` reasons. A bucket that falls into "other" means the funnel
cannot name why candidates die, which defeats its purpose, so the guard tests read the source and
fail when a reason the code can emit is not recognised.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services import options_funnel as f

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ── execution-stage reasons ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("frequency_controller: below_min_confidence: 0.62 < 0.70 (aggressive)", "frequency_controller: below_min_confidence"),
    ("frequency_controller: below_min_confidence: 0.58 < 0.70 (aggressive)", "frequency_controller: below_min_confidence"),
    ("frequency_controller: daily_cap: 10/10 for aggressive", "frequency_controller: daily_cap"),
    ("frequency_controller: pop_below_floor: 0.66 < 0.70 (aggressive)", "frequency_controller: pop_below_floor"),
    ("frequency_controller: risk_score_too_high: 52 > 40 (aggressive)", "frequency_controller: risk_score_too_high"),
    ("frequency_controller: non_positive_ev: -0.12", "frequency_controller: non_positive_ev"),
    ("liquidity_gate: spread_too_wide (12.5%)", "liquidity_gate: spread_too_wide"),
    ("liquidity_gate: open_interest_too_low (40)", "liquidity_gate: open_interest_too_low"),
    ("liquidity_gate: 0dte_near_close (12min left)", "liquidity_gate: 0dte_near_close"),
    ("0dte_autopilot_disabled", "0dte_autopilot_disabled"),
    ("kill_switch", "kill_switch"),
    ("guardrail: Daily loss limit hit: 5.10% (limit: -5.00%). Trading suspended", "guardrail: daily loss limit hit"),
    ("guardrail: Max drawdown limit hit: 31.00% from peak (limit: 30.00%).", "guardrail: max drawdown limit hit"),
    ("capital_preservation: strategy 'iron_condor' not allowed in capital_preservation mode",
     "capital_preservation: strategy 'iron_condor' not allowed in capital_preservation mode"),
    ("portfolio_gate: max_positions 5/5", "portfolio_gate: max_positions"),
    ("owner_ambiguous: no operator organisation designated", "owner_ambiguous"),
    ("market_closed: weekend", "market_closed: weekend"),
])
def test_execution_reasons_collapse_to_a_stable_bucket(raw, expected):
    assert f.bucket_execution_reason(raw) == expected


def test_numbers_do_not_split_one_reason_into_many():
    seen = {f.bucket_execution_reason(f"frequency_controller: below_min_confidence: 0.{n} < 0.70 (aggressive)") for n in range(50, 70)}
    assert seen == {"frequency_controller: below_min_confidence"}


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_a_missing_execution_reason_is_unspecified_not_a_crash(raw):
    assert f.bucket_execution_reason(raw) == "unspecified"


# ── scan-stage reasons ───────────────────────────────────────────────────────

def test_a_combined_entry_rule_row_counts_once_per_tried_strategy():
    row = ("bull_put_spread: Price below 20-day SMA — no bullish bias; "
           "bear_call_spread: IV rank too low: 22.0 (need >30)")
    assert f.bucket_scan_reason(row) == ["entry_rule: price_vs_20day_average", "entry_rule: iv_rank_too_low"]


@pytest.mark.parametrize("raw,expected", [
    ("insufficient price history", ["insufficient_price_history"]),
    ("no usable spread width", ["no_usable_spread"]),
    ("no usable spread (width=0.0, credit=$0.00)", ["no_usable_spread"]),
    ("debit too small ($0.03)", ["debit_too_small"]),
    ("sized to 0 contracts (max loss $812)", ["sized_to_zero_contracts"]),
    ("SPY concentration would be 31.2% of portfolio (max 25%)", ["underlying_concentration"]),
    ("sector 'Tech' concentration would be 44.0% of portfolio (max 40%)", ["sector_concentration"]),
    ("conflicts with this ticker's own equity signal (SELL, 12min old)", ["equity_signal_conflict"]),
    ("scan error — see server log", ["scan_error"]),
    ("bull_put_spread: IV rank too low: 3.2 (need >5)", ["entry_rule: iv_rank_too_low"]),
    ("bull_put_spread: RSI too weak: 41.0 (need >45)", ["entry_rule: rsi"]),
    ("bear_call_spread: RSI too high: 61.0 (need <55)", ["entry_rule: rsi"]),
    ("bull_call_debit_spread: RSI not strong enough: 44.0 (need >55)", ["entry_rule: rsi"]),
    ("bull_call_debit_spread: IV rank too high: 100.0 (need <100 for debit spread)", ["entry_rule: iv_rank_too_high"]),
    ("bull_put_spread: Could not find 0.20 delta put strike", ["entry_rule: no_strike_at_target_delta"]),
    ("iron_condor: VIX out of range: 31.2 (need 15–25)", ["entry_rule: vix_out_of_range"]),
    ("bull_call_debit_spread: Debit spread suspended in capital preservation mode", ["guardrail: capital_preservation_mode"]),
])
def test_scan_reasons_map_to_named_buckets(raw, expected):
    assert f.bucket_scan_reason(raw) == expected


def test_the_ai_scorer_rejection_keeps_its_reason_but_not_its_numbers():
    a = f.bucket_scan_reason("AI scorer: Score 0.37 is within uncertainty band (threshold=0.35 ± 0.05)")[0]
    b = f.bucket_scan_reason("AI scorer: Score 0.41 is within uncertainty band (threshold=0.35 ± 0.05)")[0]
    assert a == b and a.startswith("ai_scorer_rejected: ")


def test_a_guardrail_reason_passed_through_by_a_strategy_is_named_as_a_guardrail():
    out = f.bucket_scan_reason("bull_put_spread: Daily loss limit hit: 5.1% (limit: -5.00%). Trading suspended")
    assert out and out[0].startswith("guardrail: ")


def test_an_unrecognised_reason_is_kept_visible_as_other_not_dropped():
    out = f.bucket_scan_reason("something entirely new happened")
    assert out == ["other: something entirely new happened"]


def test_missing_scan_reason_is_unspecified():
    assert f.bucket_scan_reason(None) == ["unspecified"] == f.bucket_scan_reason("")


# ── guards: reasons the code can emit must be recognised ─────────────────────

def test_every_strategy_rejection_literal_in_the_code_is_recognised():
    src = (BACKEND / "app/services/strategy_engine.py").read_text()
    # Entry rejections are Signal(...) objects; ExitDecision(...) reasons ('profit target reached',
    # 'DTE time stop') are exits, not reasons a candidate failed to enter.
    literals = re.findall(r'\bSignal\((?:[^()]|\([^()]*\))*?reason=f?"([^"]+)"', src, re.S)
    rejections = [t for t in literals if t != "All entry conditions met"]
    assert len(rejections) >= 10, "read nothing: strategy_engine.py moved"
    unknown = [t for t in rejections if f.bucket_scan_reason(f"bull_put_spread: {t}")[0].startswith("other:")]
    assert not unknown, f"the funnel cannot name these strategy rejections: {unknown}"


def test_every_scan_rejection_the_scan_records_is_recognised():
    src = (BACKEND / "app/main.py").read_text()
    literals = re.findall(r'_record_options_rejection\(\s*(?:symbol,\s*)?f?"((?:[^"\\]|\\.)*)"', src)
    assert len(literals) >= 6, "read nothing: the scan's rejection calls moved"
    prefixes = [re.sub(r"\{[^}]*\}", "X", lit) for lit in literals]     # f-string fields -> a placeholder
    unknown = [p for p in prefixes if f.bucket_scan_reason(p)[0].startswith("other:")]
    assert not unknown, f"the funnel cannot name these scan rejections: {unknown}"


def test_every_execution_block_prefix_the_code_emits_is_not_unspecified():
    src = (BACKEND / "app/api/routes/trade_desk.py").read_text()
    prefixes = set(re.findall(r'_blocked\(\s*f?"([a-z0-9_]+)', src))
    assert len(prefixes) >= 8, "read nothing: _blocked reasons moved"
    assert "frequency_controller" in prefixes and "liquidity_gate" in prefixes
    for p in prefixes:
        assert f.bucket_execution_reason(f"{p}: something") != "unspecified"


# ── verdict ──────────────────────────────────────────────────────────────────

BASE = dict(mode="autopilot", scanned=100, qualified=10, dispatched=10, submitted=0,
            queued_pending=0, queued_approved=0,
            top_scan={"reason": "entry_rule: rsi", "count": 60},
            top_block={"reason": "frequency_controller: below_min_confidence", "count": 9})


@pytest.mark.parametrize("override,code", [
    ({"submitted": 2}, "trading"),
    ({"scanned": 0, "qualified": 0}, "no_data"),
    ({"qualified": 0, "dispatched": 0}, "nothing_qualifies"),
    ({"mode": "manual", "dispatched": 0}, "mode_manual"),
    ({"mode": "copilot", "dispatched": 0, "queued_pending": 10}, "waiting_for_approval"),
    ({"dispatched": 0}, "qualified_but_never_dispatched"),
    ({}, "blocked_at_execution"),
])
def test_each_verdict_branch(override, code):
    assert f.verdict(**{**BASE, **override})["code"] == code


def test_a_submitted_order_beats_every_other_explanation():
    assert f.verdict(**{**BASE, "submitted": 1, "mode": "manual", "qualified": 0})["code"] == "trading"


def test_manual_mode_is_not_blamed_when_nothing_even_qualified():
    assert f.verdict(**{**BASE, "mode": "manual", "qualified": 0, "dispatched": 0})["code"] == "nothing_qualifies"


def test_the_verdict_names_the_biggest_reason_and_its_count():
    assert "frequency_controller: below_min_confidence (9)" in f.verdict(**BASE)["text"]
    assert "entry_rule: rsi (60)" in f.verdict(**{**BASE, "qualified": 0, "dispatched": 0})["text"]


def test_copilot_with_an_approval_is_not_reported_as_waiting():
    v = f.verdict(**{**BASE, "mode": "copilot", "queued_approved": 1, "queued_pending": 9})
    assert v["code"] == "blocked_at_execution"
