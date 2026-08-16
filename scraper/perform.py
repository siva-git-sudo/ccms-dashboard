"""Who performed, on the department's own definition of progress.

    python3 perform.py                          # every circle, latest window
    python3 perform.py --level division
    python3 perform.py --circle "Bengaluru Circle" --level division
    python3 perform.py --from 2026-08-11 --to 2026-08-13
    python3 perform.py --html                   # also write public/performance.html

THE RULE
--------
Progress is two things, per the review:
    1. a case reaching the hearing stage        -> increase in hearing is good
    2. a final order being complied with        -> decrease in final order pending is good

    score = hearing gained + final orders cleared

THE GUARD
---------
Applied naively that rule is gameable by simply handing files to another
office. On 12 August Chikkaballapura's final-order count fell by 10 and it
completed nothing -- the files went to the HQ Legal Cell. Statewide the stock
did not move by a single case. A rule that rewards "reduction in final order
pending" would have scored that as the best performance in the circle.

So a reduction only counts where a completion backs it:

    final orders cleared   = min(fall in final-order stock, cases completed)
    final orders passed on = the rest of the fall

and the same test on the other side: a rise in hearing only counts where the
office did not simply receive files, measured by its book total.

    book total = pending + completed
    transfers  = change in book total - arrivals

A case can only leave an office's book total by being transferred out, so this
is a direct measure of files changing hands, computable from stock data alone.

WHAT TO RANK ON
---------------
Circle. Inside a circle, transfers between its divisions cancel, so the circle
score is honest by construction. Division and officer scores are worth asking
questions about; they are not scores.
"""

from __future__ import annotations

import argparse
import json
import os

import analytics as an
import stage_map as sm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S = sm.SPINE
PEND = S[:5]


def circle_map():
    with open(os.path.join(ROOT, "public", "data.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    return {d["name"]: (d.get("circle") or "Unassigned") for d in meta["divisions"]}


def collect(dates, level, circles, only_circle=None):
    """{unit: [per-date totals]} at circle, division or officer level."""
    per = {}
    for dt in dates:
        day, _ = an.load_date(dt)
        for (div, sec, post), v in day.items():
            circ = circles.get(div)
            if circ is None:
                continue                      # outside divisions.json, out of scope
            if only_circle and circ != only_circle:
                continue
            key = {"circle": circ,
                   "division": div,
                   "officer": f"{div} / {post}"}[level]
            slot = per.setdefault(key, {}).setdefault(dt, {x: 0 for x in S})
            for x in S:
                slot[x] += v.get(x, 0)
            slot["final_order"] = slot.get("final_order", 0) + v.get("final_order", 0)
            slot["intake"] = slot.get("intake", 0) + v.get("intake", 0)
    return per


def score(days, dates):
    """Apply the rule, then subtract what was only a change of hands."""
    a, b = days.get(dates[0]), days.get(dates[-1])
    if not a or not b:
        return None
    book_a = sum(a[x] for x in PEND) + a["completed"]
    book_b = sum(b[x] for x in PEND) + b["completed"]
    intake = sum(days[dt].get("intake", 0) for dt in dates[1:] if dt in days)
    transfers = (book_b - book_a) - intake
    completed = b["completed"] - a["completed"]

    hearing_gain = b["hearing"] - a["hearing"]
    fo_fall = a.get("final_order", 0) - b.get("final_order", 0)

    # a fall in final orders is only progress as far as completions explain it
    fo_cleared = max(min(fo_fall, completed), 0)
    fo_passed_on = max(fo_fall - fo_cleared, 0)

    # a rise in hearing is discounted by files the office simply received
    hearing_credited = hearing_gain
    if hearing_gain > 0 and transfers > 0:
        hearing_credited = max(hearing_gain - transfers, 0)

    return {
        "score": hearing_credited + fo_cleared,
        "hearing_gain": hearing_gain,
        "hearing_credited": hearing_credited,
        "final_order_fall": fo_fall,
        "final_orders_cleared": fo_cleared,
        "final_orders_passed_on": fo_passed_on,
        "completed": completed,
        "transfers": transfers,
        "pending": sum(b[x] for x in PEND),
        "hearing_now": b["hearing"],
        "final_order_now": b.get("final_order", 0),
    }


def run(dates, level, only_circle=None):
    circles = circle_map()
    per = collect(dates, level, circles, only_circle)
    out = []
    for name, days in per.items():
        r = score(days, dates)
        if r and (r["pending"] or r["completed"]):
            out.append({"name": name, **r})
    out.sort(key=lambda r: (-r["score"], -r["final_orders_cleared"], r["pending"]))
    return out


def table(rows, title, dates):
    w = f"{dates[0]} to {dates[-1]}"
    print(f"\n{title}  ({w})")
    print("-" * 108)
    print(f"  {'#':>2} {'unit':34}{'SCORE':>7}{'hearing':>9}{'FO clrd':>9}"
          f"{'passed on':>11}{'finished':>10}{'moved':>8}{'pending':>9}")
    print("-" * 108)
    for i, r in enumerate(rows, 1):
        flag = "  <- passed files on" if r["final_orders_passed_on"] > 0 else ""
        print(f"  {i:>2} {r['name'][:32]:34}{r['score']:>7}{r['hearing_credited']:>+9}"
              f"{r['final_orders_cleared']:>9}{r['final_orders_passed_on']:>11}"
              f"{r['completed']:>10}{r['transfers']:>+8}{r['pending']:>9}{flag}")
    t = {k: sum(r[k] for r in rows) for k in
         ("score", "hearing_credited", "final_orders_cleared",
          "final_orders_passed_on", "completed", "pending")}
    print("-" * 108)
    print(f"     {'TOTAL':34}{t['score']:>7}{t['hearing_credited']:>+9}"
          f"{t['final_orders_cleared']:>9}{t['final_orders_passed_on']:>11}"
          f"{t['completed']:>10}{'':>8}{t['pending']:>9}")


def main():
    ap = argparse.ArgumentParser()
    dates = an.available_dates()
    ap.add_argument("--from", dest="dfrom", default=dates[0])
    ap.add_argument("--to", dest="dto", default=dates[-1])
    ap.add_argument("--level", default="circle",
                    choices=["circle", "division", "officer"])
    ap.add_argument("--circle", default=None)
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    window = [d for d in dates if args.dfrom <= d <= args.dto]
    if len(window) < 2:
        raise SystemExit("need at least two snapshots in the window")

    rows = run(window, args.level, args.circle)
    shown = rows[:args.top] if args.top else rows
    title = f"PERFORMANCE BY {args.level.upper()}" + (f" — {args.circle}" if args.circle else "")
    table(shown, title, window)

    zero = [r for r in rows if r["score"] == 0]
    passed = [r for r in rows if r["final_orders_passed_on"] > 0]
    print(f"\n  score = hearing gained + final orders cleared, both net of files changing hands")
    print(f"  {len(rows) - len(zero)} of {len(rows)} units scored above zero")
    if passed:
        n = sum(r["final_orders_passed_on"] for r in passed)
        print(f"  {n} final orders left a unit without being complied with "
              f"({', '.join(r['name'] for r in passed[:4])})")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, ensure_ascii=False)
        print(f"  written: {args.json}")


if __name__ == "__main__":
    main()
