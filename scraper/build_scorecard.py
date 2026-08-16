"""Add gate-throughput scorecard blocks to public/data.json.

The analytics dashboard reads, for every division and every officer post:

    scorecard: { d: {...}, w: {...}, m: {...} }

with G1..G4 crossings, conversion rates, a composite score and the
statistical flags. Nothing produced that block until now, so every gate
rendered as "-". This script computes it from the raw SSRS XML using the
corrected stage crosswalk and writes it back into data.json.

Purely additive: existing keys are left untouched, so index.html keeps
working exactly as before.

    python3 build_scorecard.py
"""

from __future__ import annotations

import json
import os

import analytics as an

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_JSON = os.path.join(ROOT, "public", "data.json")
DATA_JS = os.path.join(ROOT, "public", "data.js")

# Gate -> the scorecard field name the dashboard expects.
GATE_FIELD = {
    "out_of_no_action": "G1_intake_reduced",
    "out_of_preparation": "G2_lco_dispatched",
    "out_of_hearing": "G3_hearing_reached",
    "out_of_compliance": "G4_compliance_closed",
}
# Denominator for each gate's conversion rate: every case that was upstream of
# that boundary when the window opened -- i.e. at that stage OR any earlier one.
#
# It is tempting to use just the stage's own opening stock, but that is wrong.
# A case can cross several boundaries inside one window: picked up on Monday,
# paperwork out on Tuesday, listed on Wednesday. Bengaluru Circle recorded 6
# cases crossing the hearing boundary while holding 0 in the hearing stage at
# open -- they came from further upstream. Measuring against the stage's own
# stock produced a 0% rate on 6 real crossings, or divided by zero.
GATE_DENOM = {
    "out_of_no_action": ["no_action"],
    "out_of_preparation": ["no_action", "preparation"],
    "out_of_hearing": ["no_action", "preparation", "hearing"],
    "out_of_compliance": ["no_action", "preparation", "hearing", "compliance"],
}
# Weights for the composite. Compliance carries the most because that is
# where the largest stock sits and where delay is most visible to a court.
GATE_WEIGHT = {
    "out_of_no_action": 0.20,
    "out_of_preparation": 0.25,
    "out_of_hearing": 0.25,
    "out_of_compliance": 0.30,
}

PERIOD_SPAN = {"d": 1, "w": 7, "m": 30}


def pick_periods(dates: list[str]) -> dict:
    """Choose a comparison date for each period from what actually exists.

    With a short history the weekly and monthly windows fall back to the
    earliest snapshot available and report the true span, so the dashboard
    never implies more history than there is.
    """
    latest = dates[-1]
    out = {}
    for key, want in PERIOD_SPAN.items():
        best = None
        for d in dates[:-1]:
            span = an._days_between(d, latest)
            if span <= 0:
                continue
            if best is None or abs(span - want) < abs(best[1] - want):
                best = (d, span)
        out[key] = {"from": best[0], "to": latest, "span": best[1],
                    "requested_span": want, "exact": best[1] == want} if best else None
    return out


def score_unit(u: dict, weight_map=GATE_WEIGHT) -> dict:
    """Turn one analytics unit into the scorecard block the dashboard reads."""
    block = {}
    weighted, wsum = 0.0, 0.0
    for gate, field in GATE_FIELD.items():
        k = u["gates"][gate]
        block[field] = k
        # Cases that arrived during the window are also eligible to cross --
        # a case can be received and pushed past No Action the same week.
        # Without this, a unit that opened with nothing at a stage but cleared
        # fresh arrivals scored 0% on real work.
        denom = sum(u["opening"].get(s, 0) for s in GATE_DENOM[gate]) + u["intake"]
        # denom == 0 with k > 0 is not a 0% rate, it is an undefined one: the
        # case arrived from another division's jurisdiction rather than from
        # upstream in this unit's own pipeline. Reported as null, shown as n/a,
        # and excluded from the composite rather than scored as failure.
        rate = (max(k, 0) / denom) if denom > 0 else None
        block["rate_" + field.split("_")[0].lower() + "_pct"] = (
            round(100 * rate, 1) if rate is not None else None
        )
        if rate is not None:
            weighted += weight_map[gate] * rate
            wsum += weight_map[gate]

    # ---- the five numbers a review meeting asks for, all case types combined
    #
    # New cases added is the report's own "Cases Received As On Yesterday",
    # accumulated over every day in the window. The accounting identity
    #     arrivals = change in pending + change in completed
    # is kept alongside as a cross-check: where the two disagree, cases left
    # the pending set without being completed -- withdrawn, transferred to
    # another department, or corrected in CCMS. Reported separately rather
    # than blended, because a reviewer asking "how many new cases" wants the
    # register figure, not a residual.
    pend_stages = ("no_action", "lco_proposal", "preparation", "hearing", "compliance")
    opening_pending = sum(u["opening"].get(s, 0) for s in pend_stages)
    closing_pending = sum(u["closing"].get(s, 0) for s in pend_stages)
    completed_delta = u["closing"].get("completed", 0) - u["opening"].get("completed", 0)
    implied = (closing_pending - opening_pending) + completed_delta
    moved = (u["gates"]["out_of_no_action"] + u["gates"]["out_of_lco_proposal"]
             + u["gates"]["out_of_preparation"] + u["gates"]["out_of_compliance"])

    # ---- progress score, on the department's own definition:
    #      a case reaching hearing is progress, a final order complied is
    #      progress. Both discounted for files that merely changed hands --
    #      see perform.py for why that guard is not optional.
    transfers = (closing_pending + u["closing"].get("completed", 0)
                 - opening_pending - u["opening"].get("completed", 0)) - u["intake"]
    completed = u["closing"].get("completed", 0) - u["opening"].get("completed", 0)
    hearing_gain = u["closing"].get("hearing", 0) - u["opening"].get("hearing", 0)
    fo_fall = u["opening"].get("final_order", 0) - u["closing"].get("final_order", 0)
    fo_cleared = max(min(fo_fall, completed), 0)
    fo_passed_on = max(fo_fall - fo_cleared, 0)
    hearing_credited = hearing_gain
    if hearing_gain > 0 and transfers > 0:
        hearing_credited = max(hearing_gain - transfers, 0)
    block["review"] = {
        "score": hearing_credited + fo_cleared,
        "hearing_gain": hearing_gain,
        "hearing_credited": hearing_credited,
        "final_orders_cleared": fo_cleared,
        "final_orders_passed_on": fo_passed_on,
        "transfers": transfers,
        "moved": moved,
        "new_cases": u["intake"],
        "implied_new_cases": implied,
        "unexplained": implied - u["intake"],
        "no_action_closed": u["gates"]["out_of_no_action"],
        "lco_done": u["gates"]["out_of_lco_proposal"],
        "reached_hearing": u["gates"]["out_of_preparation"],
        "orders_complied": u["gates"]["out_of_compliance"],
        "still_pending": closing_pending,
        "still_no_action": u["closing"].get("no_action", 0),
    }

    block["total_gate_crossings"] = u["crossings"]
    # Composite: weighted mean of the conversion rates that were actually
    # applicable, renormalised so a division with nothing at a given stage is
    # not scored as having failed that gate.
    block["composite_score"] = round(100 * weighted / wsum, 1) if wsum > 0 else 0.0
    block["no_action_share_pct"] = (
        round(100 * u["no_action"] / u["pending_now"], 1) if u["pending_now"] else 0.0
    )
    block["net_backlog_minus_intake"] = u["net_backlog_change"] - u["intake"]

    # Statistical flags -- what makes a number trustworthy rather than loud.
    block["stock"] = {s: u["closing"].get(s, 0) for s in
                      ("no_action", "preparation", "hearing", "compliance")}
    block["exposure"] = u["exposure"]
    block["raw_rate_pct"] = round(100 * u["raw_rate"], 2)
    block["shrunk_rate_pct"] = round(100 * u["eb_rate"], 2)
    block["signal"] = u["signal"]
    block["ucl_pct"] = round(100 * u["ucl_998"], 2)
    block["lcl_pct"] = round(100 * u["lcl_998"], 2)
    block["stagnation_p"] = (
        round(u["stagnation_p"], 5) if u["stagnation_p"] is not None else None
    )
    return block


def plain_alerts(units, span, known=None, top_n=8, per_type=3):
    """Turn the statistics into sentences an officer can act on.

    A funnel plot tells you a division is a statistical outlier. It does not
    tell you to ring the DCF at Mandya. These rules do that translation: each
    alert names a unit, a number of cases, and one thing to ask about. Ranked
    by how many cases are affected, because that is what makes an alert worth
    somebody's morning.
    """
    days = f"{span} day" + ("" if span == 1 else "s")
    out = []

    for u in units:
        name, pend = u["name"], u["pending_now"]
        # units outside divisions.json are reported in the coverage banner, not
        # here -- they are deliberately out of scope for this dashboard
        if known is not None and name not in known:
            continue
        stock = u["closing"]
        no_act = stock.get("no_action", 0)
        comp = stock.get("compliance", 0)
        prep = stock.get("preparation", 0)
        k = u["crossings"]
        g = u["gates"]

        if pend >= 20 and k == 0:
            out.append({
                "severity": "high", "type": "stalled", "cases": pend, "unit": name,
                "headline": f"{name} moved nothing",
                "detail": f"All {pend} cases sat exactly where they were for {days}. "
                          f"Not one reached the next stage.",
                "ask": "Ask who is holding these files and what is blocking them.",
            })
        elif no_act >= 10:
            out.append({
                "severity": "high", "type": "untouched", "cases": no_act, "unit": name,
                "headline": f"{name} has {no_act} cases nobody has started",
                "detail": f"{no_act} of {pend} cases are still marked No Action — "
                          f"no LCO proposal, no paperwork, nothing on file.",
                "ask": "Ask for a date by which each will have an LCO proposal raised.",
            })

        if comp >= 40 and g["out_of_compliance"] <= 0:
            out.append({
                "severity": "high" if comp >= 100 else "medium", "type": "compliance", "cases": comp, "unit": name,
                "headline": f"{name} has {comp} orders awaiting compliance",
                "detail": f"The court has ruled on {comp} cases and compliance has not been "
                          f"reported back. None cleared in {days}. This is the stage that "
                          f"turns into a contempt petition.",
                "ask": "Ask which of these have compliance affidavits drafted but not filed.",
            })

        if prep >= 60 and g["out_of_preparation"] <= 0:
            out.append({
                "severity": "medium", "type": "paperwork", "cases": prep, "unit": name,
                "headline": f"{name} has {prep} cases stuck in paperwork",
                "detail": f"{prep} cases are waiting on the department's own steps — LCO "
                          f"proposal, PWR, statement of objections or affidavit. "
                          f"None moved on in {days}.",
                "ask": "Ask which are waiting on the advocate and which on the office.",
            })

        if u["signal"] == "above" and k > 0:
            out.append({
                "severity": "good", "type": "good", "cases": k, "unit": name,
                "headline": f"{name} cleared {k} cases",
                "detail": f"Well above what a division of {pend} cases would normally manage "
                          f"in {days}.",
                "ask": "Worth asking what they are doing differently.",
            })

    rank = {"high": 0, "medium": 1, "good": 2}
    out.sort(key=lambda a: (rank[a["severity"]], -a["cases"]))
    # One alert per unit, and at most `per_type` of any one kind. Without the
    # second cap the list fills with eight identical "moved nothing" lines and
    # the reader learns one fact instead of four.
    seen, counts, keep = set(), {}, []
    for a in out:
        t = a["type"]
        if a["unit"] in seen or counts.get(t, 0) >= per_type:
            continue
        seen.add(a["unit"])
        counts[t] = counts.get(t, 0) + 1
        keep.append(a)
    keep.sort(key=lambda a: (rank[a["severity"]], -a["cases"]))
    return keep[:top_n]


def top_movers(dres, ores, known=None, n=5):
    """Who performed -- offices and named officer posts, ranked on the score.

    A review sheet sorted by backlog size buries the people doing the work at
    the bottom of the page. This is the answer to "who is actually moving
    cases", which is the first thing asked in the meeting and was the one
    thing the sheet could not show.
    """
    def rank(units, split_name=False):
        out = []
        for u in units:
            name = u["name"]
            if split_name:
                parts = name.split(" / ")
                if len(parts) != 3:
                    continue
                office, section, post = parts
            else:
                office, section, post = name, None, None
            if known is not None and office not in known:
                continue
            g = u["gates"]
            moved = (g["out_of_no_action"] + g["out_of_lco_proposal"]
                     + g["out_of_preparation"] + g["out_of_compliance"])
            # rank on the department's definition of progress, not on raw
            # movement -- movement includes files that merely changed hands
            b = score_unit(u)["review"]
            if b["score"] <= 0:
                continue
            out.append({
                "office": office, "section": section, "post": post,
                "score": b["score"], "hearing_credited": b["hearing_credited"],
                "final_orders_cleared": b["final_orders_cleared"],
                "final_orders_passed_on": b["final_orders_passed_on"],
                "moved": moved,
                "no_action_closed": g["out_of_no_action"],
                "lco_done": g["out_of_lco_proposal"],
                "reached_hearing": g["out_of_preparation"],
                "orders_complied": g["out_of_compliance"],
                "still_pending": u["pending_now"],
            })
        out.sort(key=lambda x: (-x["score"], -x["final_orders_cleared"], -x["still_pending"]))
        return out[:n]

    return {"divisions": rank(dres["units"]), "officers": rank(ores["units"], True)}


def plain_summary(dres, span, known=None):
    """One paragraph, no jargon, that says how the department did."""
    st = dres
    days = f"{span} day" + ("" if span == 1 else "s")
    # Same population as the sheet below, so the headline cards and the total
    # row can never disagree.
    units = [u for u in st["units"] if known is None or u["name"] in known]
    moved = sum(1 for u in units if u["crossings"] > 0)
    still = [u for u in units if u["crossings"] == 0 and u["pending_now"] >= 15]
    still_cases = sum(u["pending_now"] for u in still)
    no_act = sum(u["closing"].get("no_action", 0) for u in units)
    comp = sum(u["closing"].get("compliance", 0) for u in units)
    # NET, not clipped to zero. Clipping each unit at zero before summing
    # double-counts inter-division transfers: the division a case moves TO
    # records a crossing, the division it moves FROM records the reversal, and
    # clipping keeps the first while discarding the second. It also makes the
    # headline card disagree with the total row underneath the sheet, which is
    # the fastest way to lose a reviewer's trust.
    def gsum(gate):
        return sum(u["gates"][gate] for u in units)

    new_cases = sum(u["intake"] for u in units)
    implied = sum(
        (sum(u["closing"].get(x, 0) for x in ("no_action", "lco_proposal", "preparation",
                                              "hearing", "compliance"))
         - sum(u["opening"].get(x, 0) for x in ("no_action", "lco_proposal", "preparation",
                                                "hearing", "compliance")))
        + (u["closing"].get("completed", 0) - u["opening"].get("completed", 0))
        for u in units
    )
    total_pending = sum(u["pending_now"] for u in units)
    return {
        "days": days,
        "total_pending": total_pending,
        "moved_units": moved,
        "total_units": len(units),
        "still_units": len(still),
        "still_cases": still_cases,
        "no_action": no_act,
        "compliance": comp,
        "new_cases": new_cases,
        "implied_new_cases": implied,
        "unexplained": implied - new_cases,
        "no_action_closed": gsum("out_of_no_action"),
        "lco_done": gsum("out_of_lco_proposal"),
        "reached_hearing": gsum("out_of_preparation"),
        "orders_complied": gsum("out_of_compliance"),
        "text": (
            f"In the last {days} the department took in {new_cases} new case"
            f"{'' if new_cases == 1 else 's'}, closed {gsum('out_of_no_action')} no-action "
            f"file{'' if gsum('out_of_no_action') == 1 else 's'}, sent "
            f"{gsum('out_of_lco_proposal')} LCO proposal"
            f"{'' if gsum('out_of_lco_proposal') == 1 else 's'}, brought "
            f"{gsum('out_of_preparation')} case"
            f"{'' if gsum('out_of_preparation') == 1 else 's'} to hearing and complied with "
            f"{gsum('out_of_compliance')} final order"
            f"{'' if gsum('out_of_compliance') == 1 else 's'}. "
            f"{total_pending:,} cases remain pending. {len(still)} of {len(units)} "
            f"offices, holding {still_cases:,} cases, did none of this."
        ),
    }


def build():
    with open(DATA_JSON, encoding="utf-8") as fh:
        data = json.load(fh)

    dates = an.available_dates()
    if len(dates) < 2:
        raise SystemExit("need at least two snapshot dates")
    periods = pick_periods(dates)

    known_names = {d.get("name") for d in data.get("divisions", [])}
    div_scores: dict[str, dict] = {}
    off_scores: dict[tuple, dict] = {}
    stats: dict[str, dict] = {}

    for pkey, p in periods.items():
        if not p:
            continue
        dres = an.analyse(p["from"], p["to"], "division")
        ores = an.analyse(p["from"], p["to"], "officer")

        for u in dres["units"]:
            div_scores.setdefault(u["name"], {})[pkey] = score_unit(u)
        for u in ores["units"]:
            parts = u["name"].split(" / ")
            if len(parts) == 3:
                off_scores.setdefault(tuple(parts), {})[pkey] = score_unit(u)

        stats[pkey] = {
            "from": p["from"], "to": p["to"], "span": p["span"],
            "requested_span": p["requested_span"], "exact": p["exact"],
            "theta_pct": round(100 * dres["theta"], 3),
            "phi": round(dres["phi"], 3),
            "tau2": dres["tau2"],
            "total_pending": dres["total_pending"],
            "total_crossings": dres["total_crossings"],
            "total_intake": dres["total_intake"],
            "gini": round(dres["gini"], 3),
            "top10pct_share": round(dres["top10pct_share"], 3),
            "n_units": dres["n_units"],
            # closing stock at each spine stage -- what the four gate cards show
            "stage_stock": {
                s: sum(u["closing"].get(s, 0) for u in dres["units"])
                for s in ("no_action", "preparation", "hearing", "compliance", "completed")
            },
            "funnel": [
                {"name": u["name"], "n": u["exposure"], "k": u["crossings"],
                 "raw": round(100 * u["raw_rate"], 3),
                 "eb": round(100 * u["eb_rate"], 3),
                 "signal": u["signal"]}
                for u in dres["units"]
            ],
            "map_problems": dres["map_problems"],
            "alerts": plain_alerts(dres["units"], p["span"], known_names),
            "summary": plain_summary(dres, p["span"], known_names),
            "top_movers": top_movers(dres, ores, known_names),
        }

    matched_d = matched_u = missed_d = missed_u = 0
    for div in data.get("divisions", []):
        sc = div_scores.get(div.get("name"))
        if sc:
            div["scorecard"] = sc
            matched_d += 1
        else:
            div["scorecard"] = {}
            missed_d += 1
        for user in div.get("users", []):
            key = (div.get("name"), user.get("section"), user.get("post"))
            usc = off_scores.get(key)
            if usc:
                user["scorecard"] = usc
                matched_u += 1
            else:
                user["scorecard"] = {}
                missed_u += 1

    data["stats"] = stats
    data["scorecard_periods"] = periods

    with open(DATA_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    with open(DATA_JS, "w", encoding="utf-8") as fh:
        fh.write("window.CCMS_DATA = ")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    print("periods:")
    for k, p in periods.items():
        print(f"  {k}: {p['from']} -> {p['to']} span={p['span']}d "
              f"(wanted {p['requested_span']}d){'' if p['exact'] else '  [FALLBACK]'}"
              if p else f"  {k}: unavailable")
    print(f"divisions scored {matched_d}, unmatched {missed_d}")
    print(f"officers  scored {matched_u}, unmatched {missed_u}")
    print("written:", DATA_JSON, "and data.js")


if __name__ == "__main__":
    build()
