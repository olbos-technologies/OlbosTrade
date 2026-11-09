"""Why a holding has no trade row the Positions screen will show.

Written for three live positions that printed the strategy "UNKNOWN". The word means
the screen found no OPEN row it was allowed to show, and there are several different
reasons for that. Each is pinned here, because the response to each is different, and
because the dangerous mistake is to call a real problem "just a label".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.services import position_trade_diagnosis as pd_
from app.services.position_trade_diagnosis import BrokerPosition, TradeRow, diagnose
from app.services.position_trade_report import build_items, summarise

ME = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")        # the signed-in operator org
OTHER = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")
OPERATOR = ME


def row(status="open", org=None, strategy="equity", cls="equity", direction=1, qty=10, exit_date=None, rid="r1"):
    return TradeRow(rid, status, strategy, cls, direction, qty, org,
                    datetime(2026, 11, 5, tzinfo=timezone.utc), exit_date, "autopilot")


def held(qty=10, cls="equity", sym="AMZN"):
    return BrokerPosition(sym, cls, qty)


def code(broker, rows, viewer=ME, operator=OPERATOR):
    return diagnose(broker, rows, viewer, operator).code


# ── the reasons, one by one ────────────────────────────────────────────────

def test_an_open_row_of_your_own_that_agrees_is_tracked():
    assert code(held(10), [row(org=ME)]) == pd_.TRACKED


def test_an_unattributed_row_is_hidden_from_the_positions_page_but_not_from_the_desk():
    """The likely cause of 'UNKNOWN': the row predates ownership (organization_id NULL),
    the signed-in account has its own organization, and the Positions screen is strict."""
    d = diagnose(held(10), [row(org=None)], ME, OPERATOR)
    assert d.code == pd_.HIDDEN and d.severity == "attention"
    assert "unattributed" in d.explanation and "display scope" in d.explanation


def test_a_row_owned_by_another_organization_is_not_the_harmless_case():
    d = diagnose(held(10), [row(org=OTHER)], ME, OPERATOR)
    assert d.code == pd_.OTHER_OWNER
    assert "another organization" in d.explanation


def test_with_no_designated_operator_an_unattributed_row_is_still_strict_for_an_organization():
    # nobody is the operator, so nothing widens the unattributed rows for ME
    assert code(held(10), [row(org=None)], viewer=ME, operator=None) == pd_.OTHER_OWNER


def test_the_single_operator_scope_matches_unattributed_rows_exactly():
    assert code(held(10), [row(org=None)], viewer=None, operator=None) == pd_.TRACKED


def test_a_pending_row_is_waiting_for_its_fill_not_a_problem():
    d = diagnose(held(10), [row(status="pending", org=ME)], ME, OPERATOR)
    assert d.code == pd_.PENDING and d.severity == "info"


def test_rows_that_all_say_closed_while_the_broker_holds_it_is_named_with_the_exit_date():
    d = diagnose(held(10), [row(status="closed", org=ME, exit_date=datetime(2026, 11, 6, tzinfo=timezone.utc))], ME, OPERATOR)
    assert d.code == pd_.ONLY_CLOSED and "2026-11-06" in d.explanation


def test_no_row_at_all_points_at_the_unrecorded_entry_marker():
    d = diagnose(held(10), [], ME, OPERATOR)
    assert d.code == pd_.NO_ROW and "ENTRY_UNRECORDED" in d.explanation


def test_a_row_on_the_wrong_side_is_a_direction_mismatch_never_called_tracked():
    assert code(held(-337), [row(org=ME, direction=1, qty=337)]) == pd_.DIRECTION_MISMATCH
    assert code(held(5), [row(org=ME, direction=-1, qty=5)]) == pd_.DIRECTION_MISMATCH


def test_a_row_of_the_wrong_size_is_a_quantity_mismatch():
    d = diagnose(held(337), [row(org=ME, direction=1, qty=300)], ME, OPERATOR)
    assert d.code == pd_.QUANTITY_MISMATCH and "+300" in d.explanation and "+337" in d.explanation


def test_a_short_that_matches_is_tracked():
    assert code(held(-337), [row(org=ME, direction=-1, qty=337)]) == pd_.TRACKED


def test_two_open_rows_are_added_before_comparing():
    assert code(held(15), [row(org=ME, qty=10, rid="a"), row(org=ME, qty=5, rid="b")]) == pd_.TRACKED


def test_an_unknown_direction_is_never_guessed_to_be_long():
    # direction 0: cannot be compared by sign, so it must not report a mismatch either
    assert code(held(-10), [row(org=ME, direction=0, qty=10)]) == pd_.TRACKED


def test_option_positions_are_not_compared_by_size_or_side():
    assert code(held(2, cls="options"), [row(org=ME, cls="options", strategy="bull_put_spread", direction=0, qty=1)]) == pd_.TRACKED


def test_an_equity_row_does_not_explain_an_option_position_in_the_same_name():
    assert code(held(2, cls="options"), [row(org=ME, cls="equity")]) == pd_.NO_ROW


def test_an_open_row_with_nothing_at_the_broker_is_reported_from_the_other_side():
    d = diagnose(None, [row(org=ME)], ME, OPERATOR)
    assert d.code == pd_.ROW_NO_BROKER
    with pytest.raises(ValueError):
        diagnose(None, [row(status="closed", org=ME)], ME, OPERATOR)


def test_every_code_has_a_severity():
    codes = {pd_.TRACKED, pd_.QUANTITY_MISMATCH, pd_.DIRECTION_MISMATCH, pd_.HIDDEN, pd_.OTHER_OWNER,
             pd_.PENDING, pd_.ONLY_CLOSED, pd_.NO_ROW, pd_.ROW_NO_BROKER}
    assert codes == set(pd_.SEVERITY)
    assert pd_.SEVERITY[pd_.TRACKED] == "ok"


# ── the visibility rules the Positions page really applies ──────────────────

def test_none_is_a_scope_not_a_wildcard():
    assert pd_.visible_to_positions_page(row(org=None), None) is True
    assert pd_.visible_to_positions_page(row(org=None), ME) is False
    assert pd_.visible_to_positions_page(row(org=ME), None) is False


def test_the_owner_label_reads_from_the_viewers_side():
    assert pd_.owner_label(ME, ME, OPERATOR) == "yours"
    assert "unattributed" in pd_.owner_label(None, ME, OPERATOR)
    assert pd_.owner_label(OTHER, ME, OPERATOR) == "another organization"
    assert pd_.owner_label(OPERATOR, OTHER, OPERATOR) == "the operator organization"


# ── the assembled report ───────────────────────────────────────────────────

def _report():
    positions = [held(7, sym="AMZN"), held(-337, sym="FER"), held(-18, sym="SPCX"), held(5, sym="QQQ")]
    rows = {
        "AMZN": [row(org=None, rid="amzn")],                      # hidden from the page
        "FER": [row(status="closed", org=ME, direction=-1, qty=337, rid="fer", exit_date=datetime(2026, 11, 6, tzinfo=timezone.utc))],
        "SPCX": [],                                              # never recorded
        "QQQ": [row(org=ME, qty=5, rid="qqq")],                  # tracked
        "GHOST": [row(org=ME, rid="ghost")],                     # open row, nothing at the broker
    }
    return build_items(positions, rows, {"SPCX": [{"state": "unknown", "asset_class": "equity", "claimed_at": None, "reason": "submit timed out"}]}, ME, OPERATOR)


def test_the_report_gives_each_holding_its_reason_and_what_the_page_shows():
    by = {i["symbol"]: i for i in _report()}
    assert by["AMZN"]["code"] == pd_.HIDDEN and by["AMZN"]["positions_page_shows"] == "UNKNOWN"
    assert by["FER"]["code"] == pd_.ONLY_CLOSED and by["FER"]["positions_page_shows"] == "UNKNOWN"
    assert by["SPCX"]["code"] == pd_.NO_ROW and by["SPCX"]["claims"][0]["state"] == "unknown"
    assert by["QQQ"]["code"] == pd_.TRACKED and by["QQQ"]["positions_page_shows"] == "equity"
    assert by["GHOST"]["code"] == pd_.ROW_NO_BROKER and by["GHOST"]["broker_quantity"] is None


def test_things_that_need_a_look_come_first_and_the_summary_counts_them():
    items = _report()
    assert items[-1]["symbol"] == "QQQ" and items[-1]["severity"] == "ok"
    assert all(i["severity"] == "attention" for i in items[:-1])
    assert summarise(items) == {"holdings": 4, "tracked": 1, "attention": 4, "rows_without_position": 1}


def test_every_row_says_who_owns_it_and_whether_the_page_can_show_it():
    amzn = next(i for i in _report() if i["symbol"] == "AMZN")
    t = amzn["trades"][0]
    assert t["owner"].startswith("unattributed") and t["shown_on_positions_page"] is False
