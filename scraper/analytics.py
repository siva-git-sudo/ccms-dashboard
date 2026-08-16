"""Performance analytics over the CCMS daily snapshots.

Reads the raw SSRS XML exports directly (not the snapshot JSON), maps every
report onto the canonical stage spine in stage_map.py, and produces:

  1. Gate-crossing throughput   -- intake-proof measure of work actually done
  2. Funnel plot with overdispersion-adjusted control limits
  3. Empirical Bayes shrunken rates for ranking small units fairly
  4. Stagnation test            -- p-value for "moved nothing" runs
  5. Concentration statistics   -- where the backlog actually sits

WHY GATE CROSSINGS AND NOT DELTAS
---------------------------------
A stage count falling does not mean work was done: cases arrive from
upstream while others leave, and fresh intake muddies everything. But a
case only ever moves forward, so the count of cases "at stage k or beyond"
can only rise when someone pushes a case past gate k-1. New cases enter at
the first stage only, so they never inflate any later gate. See
stage_map.cumulative().

WHY NOT A LEAGUE TABLE OF RATES
-------------------------------
Measured dispersion across divisions is roughly ten times Poisson, and unit
sizes run from 2 cases to 615. Ranking raw rates under those conditions puts
small units at both ends of the table by construction. Funnel limits and
empirical Bayes shrinkage are the standard corrections and both are applied
here.

Usage:
    python3 analytics.py                      # earliest -> latest snapshot
    python3 analytics.py --from 2026-08-11 --to 2026-08-13
    python3 analytics.py --level officer
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import stage_map as sm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "analytics")

Z = {0.95: 1.959963985, 0.998: 3.090232306}


# ---------------------------------------------------------------- parsing


def _num(v):
    if v is None:
        return 0
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--"):
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return 0


def parse_report(path: str, combo: str):
    """Yield (division, section, post, mapped_row) for every leaf row.

    The SSRS XML nests MajDept > MinDept > Sec > Post. Only the Post level
    is a real accountability unit; the levels above it are subtotals and
    would double-count if summed alongside.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    ns = re.match(r"\{(.*)\}", root.tag).group(1)

    def q(tag):
        return "{%s}%s" % (ns, tag)

    rows = []
    for mindept in root.iter(q("table1_MinDeptNm")):
        division = mindept.attrib.get("MinDeptNm", "").strip()
        for sec in mindept.iter(q("table1_SecNm")):
            section = sec.attrib.get("SecNm", "").strip()
            for post in sec.iter(q("table1_PostNm")):
                items = list(post.attrib.items())
                if len(items) < 3:
                    continue
                designation = items[0][1].strip()
                values = [_num(v) for _, v in items[1:]]
                mapped = sm.map_row(values, combo)
                if mapped is None:
                    continue
                rows.append((division, section, designation, mapped))
    return rows


def load_date(date: str):
    """Load one day: {(division, section, post): spine_row} plus checks."""
    folder = os.path.join(RAW, date)
    if not os.path.isdir(folder):
        return None, []
    units = defaultdict(lambda: defaultdict(int))
    problems = []
    for path in sorted(glob.glob(os.path.join(folder, "*.xml"))):
        combo = os.path.basename(path).replace("all_departments__", "").replace(".xml", "")
        for division, section, post, mapped in parse_report(path, combo):
            ok, got, declared = sm.verify_stage_map(mapped)
            if not ok:
                problems.append(
                    {"date": date, "combo": combo, "division": division,
                     "post": post, "stage_sum": got, "declared_total": declared}
                )
            spine = sm.to_spine(mapped)
            key = (division, section, post)
            for k, v in spine.items():
                if k != "_layout":
                    units[key][k] += v
            units[key]["_layouts_" + spine["_layout"]] += 1
    return units, problems


# ------------------------------------------------------------- throughput


def gate_crossings(before: dict, after: dict):
    """Cases that crossed each canonical gate between two snapshots."""
    cb, ca = sm.cumulative(before), sm.cumulative(after)
    gates = {}
    # crossing gate i means arriving at spine stage i+1 or beyond
    for i in range(1, len(sm.SPINE)):
        stage = sm.SPINE[i]
        gates["out_of_" + sm.SPINE[i - 1]] = ca[stage] - cb[stage]
    return gates


# The five numbers a review meeting actually asks for. Each is a gate crossing,
# so each is immune to fresh intake.
GATE_KEYS = [
    "out_of_no_action",     # no actions closed
    "out_of_lco_proposal",  # LCO proposals done
    "out_of_preparation",   # draft PWR / SO work finished, matter reaches hearing
    "out_of_hearing",       # past hearing, order received
    "out_of_compliance",    # final order complied, case completed
]
GATE_LABELS = {
    "out_of_no_action": "No actions closed",
    "out_of_lco_proposal": "LCO proposals done",
    "out_of_preparation": "Reached hearing",
    "out_of_hearing": "Order received",
    "out_of_compliance": "Final orders complied",
}


# --------------------------------------------------- funnel + shrinkage


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def overdispersion(units):
    """Spiegelhalter's winsorised dispersion estimate, Poisson version.

    A COUNT, NOT A PROPORTION. One case can cross several gates inside the
    same window -- picked up on Monday, paperwork out on Tuesday -- so
    crossings are not bounded by the opening stock and a binomial model is
    simply wrong here (it produced rates above 1.0 in testing). Crossings
    are work events generated by a stock at risk, which is a rate: the
    natural model is Poisson with exposure = opening pending stock.

    phi ~ 1 means Poisson variation alone explains the spread between
    units. phi > 1.5 means units are genuinely heterogeneous and naive
    limits would flag far too many of them.
    """
    N = sum(u["exposure"] for u in units)
    K = sum(u["crossings"] for u in units)
    if N == 0:
        return 0.0, 1.0
    theta = K / N
    zs = []
    for u in units:
        n = u["exposure"]
        if n <= 0:
            continue
        se = math.sqrt(max(theta / n, 1e-12))
        zs.append((u["crossings"] / n - theta) / se)
    if len(zs) < 3:
        return theta, 1.0
    s = sorted(zs)
    lo, hi = _percentile(s, 0.10), _percentile(s, 0.90)
    w = [min(max(z, lo), hi) for z in zs]
    phi = sum(x * x for x in w) / len(w)
    return theta, max(phi, 1.0)


def funnel_limits(theta, n, phi, conf=0.998):
    """Control limits that widen as the unit gets smaller."""
    if n <= 0:
        return 0.0, 1.0
    se = math.sqrt(phi * theta / n)
    z = Z[conf]
    return max(theta - z * se, 0.0), theta + z * se


def empirical_bayes(units, theta):
    """Gamma-Poisson (negative binomial) shrinkage of each unit's rate.

    A unit with 9 cases and 3 crossings carries far less evidence than one
    with 231 cases and 10; ranking their raw rates side by side ranks noise.
    The prior variance tau2 is estimated by method of moments from the
    observed between-unit spread net of sampling variance, so the amount of
    shrinkage is learned from the data rather than chosen.

    Posterior mean = (k_i + alpha) / (n_i + beta), alpha = theta^2/tau2,
    beta = theta/tau2. Small units collapse toward theta; large units keep
    almost all of their own signal.
    """
    N = sum(u["exposure"] for u in units)
    m = len(units)
    if theta <= 0 or N <= 0 or m < 3:
        for u in units:
            u["eb_rate"], u["eb_weight"] = theta, 0.0
        return 0.0
    # method of moments: observed weighted spread minus expected Poisson part
    num = sum(u["exposure"] * (u["crossings"] / u["exposure"] - theta) ** 2
              for u in units if u["exposure"] > 0)
    denom = N - sum(u["exposure"] ** 2 for u in units) / N
    tau2 = max((num - (m - 1) * theta) / denom, 0.0) if denom > 0 else 0.0
    if tau2 <= 0:
        for u in units:
            u["eb_rate"], u["eb_weight"] = theta, 0.0
        return 0.0
    alpha, beta = theta * theta / tau2, theta / tau2
    for u in units:
        n = u["exposure"]
        u["eb_rate"] = (u["crossings"] + alpha) / (n + beta)
        u["eb_weight"] = n / (n + beta)
    return tau2


def stagnation_pvalue(exposure, days, theta):
    """P(zero crossings | this unit's size and the state rate).

    Small offices go quiet by chance; large ones do not. This separates
    the two instead of listing every idle unit as a problem.
    """
    lam = theta * exposure * days
    return math.exp(-lam)


def gini(values):
    v = sorted(x for x in values if x > 0)
    n = len(v)
    if n == 0:
        return 0.0
    total = sum(v)
    cum = 0.0
    for i, x in enumerate(v, start=1):
        cum += i * x
    return (2 * cum) / (n * total) - (n + 1) / n


# -------------------------------------------------------------- analysis


def analyse(date_from: str, date_to: str, level: str = "division"):
    before, prob_b = load_date(date_from)
    after, prob_a = load_date(date_to)
    if before is None or after is None:
        raise SystemExit("missing raw XML for one of the dates")

    days = max((_days_between(date_from, date_to)), 1)

    # Arrivals over a multi-day window are the sum of each day's "Cases
    # Received As On Yesterday", not the endpoints'. Reading only the two
    # endpoint snapshots silently drops every arrival on the days between.
    intake_by_unit = defaultdict(int)
    for d in available_dates():
        if date_from < d <= date_to:
            day, _ = load_date(d)
            if not day:
                continue
            for k, v in day.items():
                intake_by_unit[k] += v.get("intake", 0)

    def key_of(k):
        return k[0] if level == "division" else " / ".join(k)

    agg_b, agg_a = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    agg_intake = defaultdict(int)
    for k, v in before.items():
        for f, x in v.items():
            agg_b[key_of(k)][f] += x
    for k, v in after.items():
        for f, x in v.items():
            agg_a[key_of(k)][f] += x
    for k, x in intake_by_unit.items():
        agg_intake[key_of(k)] += x

    units = []
    for name in sorted(set(agg_a) & set(agg_b)):
        b, a = agg_b[name], agg_a[name]
        gates = gate_crossings(b, a)
        crossings = sum(max(gates[g], 0) for g in GATE_KEYS)
        exposure = b.get("pending", 0)
        if exposure <= 0:
            continue
        units.append({
            "name": name,
            "exposure": exposure,
            "pending_now": a.get("pending", 0),
            "intake": agg_intake.get(name, 0),
            "no_action": a.get("no_action", 0),
            "crossings": crossings,
            "gates": {g: gates[g] for g in GATE_KEYS},
            # opening stock at each spine stage -- the denominator a gate's
            # conversion rate has to be measured against
            "opening": {s: b.get(s, 0) for s in list(sm.SPINE) + ["final_order"]},
            "closing": {s: a.get(s, 0) for s in list(sm.SPINE) + ["final_order"]},
            "raw_rate": crossings / exposure,
            "net_backlog_change": a.get("pending", 0) - b.get("pending", 0),
        })

    theta, phi = overdispersion(units)
    tau2 = empirical_bayes(units, theta)

    for u in units:
        lo, hi = funnel_limits(theta, u["exposure"], phi, 0.998)
        lo95, hi95 = funnel_limits(theta, u["exposure"], phi, 0.95)
        u["lcl_998"], u["ucl_998"] = lo, hi
        u["lcl_95"], u["ucl_95"] = lo95, hi95
        r = u["raw_rate"]
        u["signal"] = "above" if r > hi else "below" if r < lo else \
                      "above_95" if r > hi95 else "below_95" if r < lo95 else "within"
        u["stagnation_p"] = stagnation_pvalue(u["exposure"], days, theta) \
            if u["crossings"] == 0 else None

    pend = [u["pending_now"] for u in units]
    total_pending = sum(pend)
    ranked = sorted(pend, reverse=True)
    cum, top10 = 0, 0
    for i, x in enumerate(ranked):
        cum += x
        if i + 1 == max(1, len(ranked) // 10):
            top10 = cum
    return {
        "date_from": date_from,
        "date_to": date_to,
        "days": days,
        "level": level,
        "theta": theta,
        "phi": phi,
        "tau2": tau2,
        "n_units": len(units),
        "total_pending": total_pending,
        "total_crossings": sum(u["crossings"] for u in units),
        "total_intake": sum(u["intake"] for u in units),
        "gini": gini(pend),
        "top10pct_share": (top10 / total_pending) if total_pending else 0,
        "units": units,
        "map_problems": prob_b + prob_a,
    }


def _days_between(a, b):
    import datetime as dt
    fa = dt.date(*map(int, a.split("-")))
    fb = dt.date(*map(int, b.split("-")))
    return (fb - fa).days


def available_dates():
    return sorted(
        d for d in os.listdir(RAW)
        if os.path.isdir(os.path.join(RAW, d)) and re.match(r"\d{4}-\d{2}-\d{2}$", d)
    )


def main():
    ap = argparse.ArgumentParser()
    dates = available_dates()
    ap.add_argument("--from", dest="dfrom", default=dates[0] if dates else None)
    ap.add_argument("--to", dest="dto", default=dates[-1] if dates else None)
    ap.add_argument("--level", default="division", choices=["division", "officer"])
    args = ap.parse_args()

    res = analyse(args.dfrom, args.dto, args.level)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"performance_{args.dfrom}_{args.dto}_{args.level}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, ensure_ascii=False)

    print(f"{args.dfrom} -> {args.dto}  ({res['days']}d, {args.level} level)")
    print(f"  units={res['n_units']}  pending={res['total_pending']}  "
          f"crossings={res['total_crossings']}  intake={res['total_intake']}")
    print(f"  theta={res['theta']:.4f}  phi={res['phi']:.2f}  "
          f"gini={res['gini']:.2f}  top10%share={res['top10pct_share']:.0%}")
    print(f"  stage-map reconciliation failures: {len(res['map_problems'])}")
    flagged = [u for u in res["units"] if u["signal"] in ("above", "below")]
    print(f"  outside 99.8% funnel limits: {len(flagged)}")
    for u in sorted(flagged, key=lambda x: -x["exposure"])[:10]:
        print(f"    {u['signal']:6} {u['name'][:38]:40} n={u['exposure']:5} "
              f"k={u['crossings']:4} raw={u['raw_rate']:.3f} eb={u['eb_rate']:.3f}")
    print(f"  written: {path}")


if __name__ == "__main__":
    main()
