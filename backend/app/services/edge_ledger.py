"""Edge Lab hypothesis ledger: the rules for writing an idea down BEFORE testing it and for
judging the result honestly. Pure functions over plain dicts, like research_lab.py; the model,
routes and tests are thin adapters around it.

WHY IT EXISTS
-------------
Most trading ideas that look good are luck: test 50 ideas on the same data and a few will
shine by chance. What separates a measured edge from a story is decided before the test:

  * the exact rule, the data and the costs are written down and LOCKED first;
  * what counts as success AND as failure is fixed first (a minimum sample, a minimum effect
    AFTER costs, and a t-statistic bar);
  * every idea tried is counted, because the more ideas tried in a family the higher the bar for
    any one of them (Bonferroni, below);
  * a failed idea stays on the record, with its numbers, so it is not quietly retried.

This module judges; it does not trade, and nothing here can promote a strategy. The Research
Lab (research_lab.py) remains the only path from an idea toward capital, and "supported" here
is evidence for starting that path, never a reason to skip it.

THE HONESTY RULES
-----------------
1. An idea registered AFTER its data was seen is `retroactive`. It can reach at most `lead`, and
   only data dated after the registration can confirm it.
2. Numbers typed in by a person are `verified` false. They can KILL an idea (nobody gains by
   inventing a failure) but can never SUPPORT one. Only a server-side evaluator may mark a
   result verified; none exists yet, so today nothing can reach `supported`. That is the point:
   the first evaluator ships with its own tests.
3. Costs come off first. A mean that does not exceed the pre-registered cost is a failure
   whatever its t-statistic.
4. `supported` and `not_supported` are final. A later result is recorded but cannot flip them;
   a new look at the same question is a new hypothesis, and it raises the family count.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import NormalDist
from typing import Any, Optional

REGISTERED = "registered"
INCONCLUSIVE = "inconclusive"
LEAD = "lead"
SUPPORTED = "supported"
NOT_SUPPORTED = "not_supported"
RETIRED = "retired"
STATUSES = (REGISTERED, INCONCLUSIVE, LEAD, SUPPORTED, NOT_SUPPORTED, RETIRED)
FINAL = frozenset({SUPPORTED, NOT_SUPPORTED, RETIRED})

#: A sample below this is noise whatever the pre-registration says.
MIN_N_FLOOR = 30
DEFAULT_ALPHA = 0.05

#: The only result source accepted today. Hand-entered, therefore never verified.
MANUAL_SOURCE = "manual_study"
#: Sources a server-side evaluator may use. Empty until one exists and is tested.
VERIFIED_SOURCES: frozenset[str] = frozenset()


def normalise_family(family: str) -> str:
    return " ".join((family or "").lower().split())


def required_t(family_size: int, alpha: float = DEFAULT_ALPHA) -> float:
    """The one-sided t (normal approximation) a result must clear, raised with every idea tried.

    With m ideas in a family, testing each at alpha/m keeps the chance that ANY of them passes by
    luck near alpha (Bonferroni). One idea needs about 1.64; ten need about 2.58; a hundred, 3.29.
    """
    m = max(int(family_size), 1)
    return NormalDist().inv_cdf(1.0 - alpha / m)


def validate_criteria(c: Any) -> list[str]:
    """Why a set of pre-registered criteria is not acceptable (empty list means acceptable)."""
    if not isinstance(c, dict):
        return ["criteria must be an object"]
    problems: list[str] = []
    for key in ("metric", "unit", "baseline"):
        if not isinstance(c.get(key), str) or not c[key].strip():
            problems.append(f"criteria.{key} is required (text)")
    n = c.get("min_n")
    if not isinstance(n, int) or isinstance(n, bool) or n < MIN_N_FLOOR:
        problems.append(f"criteria.min_n must be a whole number of at least {MIN_N_FLOOR}")
    for key, strict in (("min_effect", True), ("cost", False)):
        v = c.get(key)
        if not _is_number(v) or (strict and v <= 0) or (not strict and v < 0):
            problems.append(f"criteria.{key} must be a number " + ("above zero" if strict else "of zero or more"))
    a = c.get("alpha", DEFAULT_ALPHA)
    if not _is_number(a) or not (0 < a <= 0.2):
        problems.append("criteria.alpha must be between 0 and 0.2")
    return problems


def validate_registration(p: Any) -> list[str]:
    """Everything that must be written down before a test. Empty list means it may be locked."""
    if not isinstance(p, dict):
        return ["registration must be an object"]
    problems: list[str] = []
    for key, minimum in (("title", 5), ("statement", 20), ("rule", 20), ("data_plan", 10)):
        v = p.get(key)
        if not isinstance(v, str) or len(v.strip()) < minimum:
            problems.append(f"{key} is required (at least {minimum} characters)")
    if not normalise_family(p.get("family") or ""):
        problems.append("family is required: it is what the multiple-testing count is taken over")
    if not isinstance(p.get("horizon"), str) or not p["horizon"].strip():
        problems.append("horizon is required (for example '10 minutes')")
    uni = p.get("universe")
    if not isinstance(uni, list) or not uni or not all(isinstance(x, str) and x.strip() for x in uni):
        problems.append("universe must be a non-empty list of symbols")
    if not isinstance(p.get("retroactive"), bool):
        problems.append("retroactive must be stated: true if you have already seen the data this tests")
    problems += validate_criteria(p.get("criteria"))
    return problems


def validate_result(r: Any) -> list[str]:
    if not isinstance(r, dict):
        return ["result must be an object"]
    problems: list[str] = []
    n = r.get("n")
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        problems.append("n must be a whole number of zero or more")
    if not _is_number(r.get("mean")):
        problems.append("mean must be a number")
    t = r.get("t")
    if t is not None and not _is_number(t):
        problems.append("t must be a number or omitted")
    for key in ("sample_start", "sample_end"):
        if r.get(key) is not None and _to_date(r[key]) is None:
            problems.append(f"{key} must be a date (YYYY-MM-DD)")
    return problems


@dataclass(frozen=True)
class Verdict:
    status: str
    reasons: list[str] = field(default_factory=list)
    net_effect: Optional[float] = None
    required_t: Optional[float] = None
    verified: bool = False


def evaluate(h: dict, result: dict, family_size: int) -> Verdict:
    """The status a hypothesis takes after `result`, with the reasons. `h` is the stored
    hypothesis (criteria, retroactive, locked_at, status); `result` has passed validate_result and
    carries a server-set `verified`."""
    status = h.get("status") or REGISTERED
    if status in FINAL:
        return Verdict(status, [f"already {status}: this result is recorded but cannot change the verdict; "
                                "a new look at the question is a new hypothesis"])
    c = h["criteria"]
    verified = bool(result.get("verified"))
    n, mean, t = result["n"], float(result["mean"]), result.get("t")
    reasons: list[str] = []

    if n < c["min_n"]:
        reasons.append(f"n={n} is below the pre-registered minimum of {c['min_n']}")
        return Verdict(LEAD if status == LEAD else INCONCLUSIVE, reasons, verified=verified)

    net = mean - float(c["cost"])
    if net <= 0:
        reasons.append(f"mean {mean:g} {c['unit']} does not cover the pre-registered cost {c['cost']:g}")
        return Verdict(NOT_SUPPORTED, reasons, net_effect=net, verified=verified)
    if net < float(c["min_effect"]):
        reasons.append(f"net effect {net:g} {c['unit']} after costs is below the pre-registered "
                       f"minimum {c['min_effect']:g}")
        return Verdict(NOT_SUPPORTED, reasons, net_effect=net, verified=verified)

    need = required_t(family_size, float(c.get("alpha", DEFAULT_ALPHA)))
    if t is None or t < need:
        got = "no t-statistic given" if t is None else f"t={t:g}"
        reasons.append(f"{got} is below the {need:.2f} needed with {family_size} idea(s) tried in this family: "
                       "cannot be told apart from luck")
        return Verdict(LEAD if status == LEAD else INCONCLUSIVE, reasons, net_effect=net,
                       required_t=need, verified=verified)

    # The numbers pass. Whether that counts as evidence depends on where they came from.
    if not verified:
        reasons.append("passes the numbers, but the result was typed in, not produced by a server-side "
                       "evaluator, so it cannot be 'supported'")
        return Verdict(LEAD, reasons, net_effect=net, required_t=need, verified=False)
    if h.get("retroactive"):
        locked = _to_date(h.get("locked_at"))
        start = _to_date(result.get("sample_start"))
        if locked is None or start is None or start < locked:
            reasons.append("registered after the data was seen: only a sample that starts after "
                           f"{locked or 'registration'} can confirm it")
            return Verdict(LEAD, reasons, net_effect=net, required_t=need, verified=True)
    reasons.append(f"net effect {net:g} {c['unit']} after costs, t={t:g} against {need:.2f} required, "
                   f"n={n}, verified")
    return Verdict(SUPPORTED, reasons, net_effect=net, required_t=need, verified=True)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _to_date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None
