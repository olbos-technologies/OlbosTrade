"""The Edge Lab ledger's judging rules. Every rule here exists to stop one specific way of
fooling ourselves, so each test names the way."""
import math

import pytest

from app.services import edge_ledger as L

CRIT = {"metric": "mean 10-minute move after a drop", "unit": "bp", "baseline": "all days",
        "min_n": 50, "min_effect": 2.0, "cost": 1.5, "alpha": 0.05}


def hyp(status="registered", retroactive=False, criteria=None, locked_at="2026-11-16"):
    return {"status": status, "retroactive": retroactive, "criteria": criteria or dict(CRIT), "locked_at": locked_at}


def res(n=80, mean=6.0, t=3.0, verified=True, sample_start="2026-11-20", sample_end="2027-01-20"):
    return {"n": n, "mean": mean, "t": t, "verified": verified, "sample_start": sample_start, "sample_end": sample_end}


# ── the multiple-testing bar ─────────────────────────────────────────────────

def test_the_bar_rises_with_every_idea_tried():
    one, ten, hundred = L.required_t(1), L.required_t(10), L.required_t(100)
    assert one == pytest.approx(1.645, abs=0.01) and ten == pytest.approx(2.576, abs=0.01)
    assert hundred == pytest.approx(3.29, abs=0.02) and one < ten < hundred


def test_a_zero_or_negative_family_is_treated_as_one_idea():
    assert L.required_t(0) == L.required_t(1) == L.required_t(-3)


def test_a_result_that_clears_one_idea_does_not_clear_ten():
    assert L.evaluate(hyp(), res(t=2.0), 1).status == L.SUPPORTED
    v = L.evaluate(hyp(), res(t=2.0), 10)
    assert v.status == L.INCONCLUSIVE and "10 idea(s)" in v.reasons[0]


# ── what must be written down first ──────────────────────────────────────────

def good_registration(**over):
    p = {"title": "Dip then rebound", "statement": "After SPY falls from 10:00 to 10:30 ET it rises over 10 minutes.",
         "family": "Time of day", "rule": "Buy the spread at 10:30 ET if 10:00-10:30 was down; close after 10 minutes.",
         "universe": ["SPY", "QQQ"], "horizon": "10 minutes", "criteria": dict(CRIT),
         "data_plan": "The recorder, 2026-11-17 to 2027-01-15.", "retroactive": False}
    p.update(over)
    return p


def test_a_complete_registration_is_accepted():
    assert L.validate_registration(good_registration()) == []


@pytest.mark.parametrize("field", ["title", "statement", "family", "rule", "universe", "horizon",
                                   "criteria", "data_plan", "retroactive"])
def test_every_part_of_the_registration_is_required(field):
    p = good_registration()
    del p[field]
    assert L.validate_registration(p), field


def test_retroactive_must_be_stated_not_defaulted():
    problems = L.validate_registration(good_registration(retroactive=None))
    assert any("retroactive" in x for x in problems)


@pytest.mark.parametrize("over,needle", [
    ({"universe": []}, "universe"), ({"universe": ["SPY", ""]}, "universe"), ({"family": "   "}, "family"),
    ({"rule": "too short"}, "rule"), ({"statement": "tiny"}, "statement"),
])
def test_thin_or_blank_fields_are_rejected(over, needle):
    assert any(needle in x for x in L.validate_registration(good_registration(**over)))


@pytest.mark.parametrize("over,needle", [
    ({"min_n": 10}, "min_n"), ({"min_n": True}, "min_n"), ({"min_n": 50.5}, "min_n"),
    ({"min_effect": 0}, "min_effect"), ({"min_effect": -1}, "min_effect"), ({"min_effect": float("nan")}, "min_effect"),
    ({"cost": -0.1}, "cost"), ({"cost": "1"}, "cost"), ({"alpha": 0}, "alpha"), ({"alpha": 0.5}, "alpha"),
    ({"metric": ""}, "metric"), ({"unit": None}, "unit"), ({"baseline": " "}, "baseline"),
])
def test_unusable_criteria_are_rejected(over, needle):
    assert any(needle in x for x in L.validate_criteria({**CRIT, **over}))


def test_criteria_must_be_an_object():
    assert L.validate_criteria("nope") == ["criteria must be an object"]
    assert L.validate_registration("nope") == ["registration must be an object"]


def test_a_sample_floor_stops_a_tiny_test_being_registered():
    assert any("at least 30" in x for x in L.validate_criteria({**CRIT, "min_n": 29}))
    assert L.validate_criteria({**CRIT, "min_n": 30}) == []


# ── judging a result ─────────────────────────────────────────────────────────

def test_too_small_a_sample_is_inconclusive_not_a_verdict():
    v = L.evaluate(hyp(), res(n=49), 1)
    assert v.status == L.INCONCLUSIVE and "below the pre-registered minimum of 50" in v.reasons[0]


def test_costs_come_off_first_and_a_mean_that_does_not_cover_them_fails_whatever_its_t():
    v = L.evaluate(hyp(), res(mean=1.4, t=9.0), 1)
    assert v.status == L.NOT_SUPPORTED and "does not cover" in v.reasons[0] and v.net_effect < 0


def test_covering_the_cost_but_below_the_minimum_effect_fails():
    v = L.evaluate(hyp(), res(mean=3.0, t=9.0), 1)            # net 1.5 < min_effect 2.0
    assert v.status == L.NOT_SUPPORTED and "below the pre-registered minimum" in v.reasons[0]
    assert v.net_effect == pytest.approx(1.5)


def test_a_weak_t_is_inconclusive_and_so_is_a_missing_one():
    assert L.evaluate(hyp(), res(t=1.0), 1).status == L.INCONCLUSIVE
    v = L.evaluate(hyp(), res(t=None), 1)
    assert v.status == L.INCONCLUSIVE and "no t-statistic" in v.reasons[0]


def test_typed_in_numbers_can_never_support_an_idea():
    for mean in (3.6, 6.0, 40.0):
        for t in (3.0, 8.0, 50.0):
            for retro in (False, True):
                assert L.evaluate(hyp(retroactive=retro), res(mean=mean, t=t, verified=False), 1).status != L.SUPPORTED
    v = L.evaluate(hyp(), res(verified=False), 1)
    assert v.status == L.LEAD and "typed in" in v.reasons[0]


def test_typed_in_numbers_can_end_an_idea():
    assert L.evaluate(hyp(), res(mean=0.4, verified=False), 1).status == L.NOT_SUPPORTED


def test_verified_evidence_on_a_prospective_idea_supports_it():
    v = L.evaluate(hyp(), res(), 1)
    assert v.status == L.SUPPORTED and v.verified and "verified" in v.reasons[-1]


def test_an_idea_registered_after_seeing_the_data_needs_fresh_data():
    h = hyp(retroactive=True, locked_at="2026-11-16")
    old = L.evaluate(h, res(sample_start="2026-08-21"), 1)
    assert old.status == L.LEAD and "only a sample that starts after" in old.reasons[0]
    assert L.evaluate(h, res(sample_start=None), 1).status == L.LEAD
    assert L.evaluate(h, res(sample_start="2026-11-16"), 1).status == L.SUPPORTED
    assert L.evaluate(h, res(sample_start="2026-12-01"), 1).status == L.SUPPORTED


def test_the_dip_idea_as_it_actually_came_out():
    h = hyp(retroactive=True, criteria={**CRIT, "min_n": 30, "min_effect": 1.0, "cost": 2.0})
    v = L.evaluate(h, {"n": 36, "mean": 1.06, "t": 0.67, "verified": False}, 1)
    assert v.status == L.NOT_SUPPORTED and "does not cover" in v.reasons[0]


@pytest.mark.parametrize("final", [L.SUPPORTED, L.NOT_SUPPORTED, L.RETIRED])
def test_a_final_verdict_cannot_be_flipped_by_a_later_look(final):
    v = L.evaluate(hyp(status=final), res(), 1)
    assert v.status == final and "new hypothesis" in v.reasons[0]


def test_a_lead_stays_a_lead_when_a_later_result_is_too_small_or_weak():
    assert L.evaluate(hyp(status=L.LEAD), res(n=5), 1).status == L.LEAD
    assert L.evaluate(hyp(status=L.LEAD), res(t=0.5), 1).status == L.LEAD


def test_a_lead_can_become_supported_on_verified_fresh_evidence():
    assert L.evaluate(hyp(status=L.LEAD, retroactive=True), res(sample_start="2026-12-01"), 1).status == L.SUPPORTED


def test_nothing_can_be_verified_until_an_evaluator_exists():
    assert L.VERIFIED_SOURCES == frozenset() and L.MANUAL_SOURCE not in L.VERIFIED_SOURCES


# ── results ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"n": -1, "mean": 1.0}, {"n": 1.5, "mean": 1.0}, {"n": True, "mean": 1.0}, {"n": 5, "mean": float("inf")},
    {"n": 5, "mean": "x"}, {"n": 5, "mean": 1.0, "t": "high"}, {"n": 5, "mean": 1.0, "sample_start": "yesterday"},
])
def test_malformed_results_are_rejected(bad):
    assert L.validate_result(bad)


def test_a_well_formed_result_is_accepted():
    assert L.validate_result({"n": 36, "mean": 1.06, "t": 0.67, "sample_start": "2026-08-21"}) == []
    assert L.validate_result({"n": 0, "mean": 0.0}) == []


def test_the_ledger_imports_nothing_that_can_reach_a_broker_or_an_order():
    import inspect
    imports = [ln for ln in inspect.getsource(L).splitlines() if ln.startswith(("import ", "from "))]
    assert imports, "read no import lines: the guard would pass vacuously"
    for ln in imports:
        assert not any(w in ln for w in ("broker", "coordinator", "order", "execution", "trade_desk", "main")), ln
    assert math.isfinite(L.required_t(5))


# ── wiring ───────────────────────────────────────────────────────────────────

from app.api.routes import edge_ledger as routes  # noqa: E402


def test_every_route_requires_the_operator_key_and_is_rate_limited():
    from app.api.deps import require_api_key
    from app.api.rate_limit import rate_limit
    deps = [d.dependency for d in routes.router.dependencies]
    assert require_api_key in deps and rate_limit in deps and len(routes.router.routes) == 5


def test_the_router_is_mounted_in_the_app():
    import app.main as m
    paths = {r.path for r in m.app.routes}
    assert {"/api/edge-ledger", "/api/edge-ledger/{hypothesis_id}/results",
            "/api/edge-ledger/{hypothesis_id}/retire"} <= paths
