"""Trace how cases actually move inside one circle, and compare the parts
with the whole.

    python3 circle_report.py                      # Bengaluru Circle
    python3 circle_report.py --circle "Mysuru Circle"

WHY THIS EXISTS
---------------
Counting each division on its own double-counts every case that changes hands:
the office receiving a file records an advance, the office losing it records a
reversal, and a naive total keeps the flattering half. Roll the same offices up
into their circle and those internal transfers cancel, because the case never
left the circle. The gap between the two totals is the size of the illusion.

The one number that survives either way is a completion, because a case can
only reach Completed by actually finishing.

BOOK TOTAL
----------
    book total = pending + completed
A case can only leave an office's book total by being transferred out. So
    transfers = change in book total - arrivals
is a direct measure of files changing hands, computable from the stock data
alone. It is what exposed the 11 cases that moved from Chikkaballapura and
Bengaluru Circle to the HQ Legal Cell on 12 August without anybody working them.
"""

from __future__ import annotations

import argparse
import html
import json
import os

import analytics as an
import stage_map as sm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S = sm.SPINE
PEND = S[:5]
SHORT = {"no_action": "No action", "lco_proposal": "LCO proposal",
         "preparation": "Preparation", "hearing": "Hearing",
         "compliance": "Compliance", "completed": "Completed"}


def load_days(dates):
    return {dt: an.load_date(dt)[0] for dt in dates}


def aggregate(day, dt, pred):
    t = {x: 0 for x in S}
    t["intake"] = 0
    for k, v in day[dt].items():
        if pred(k):
            for x in S:
                t[x] += v.get(x, 0)
            t["intake"] += v.get("intake", 0)
    return t


def measure(day, dates, pred):
    d = [aggregate(day, dt, pred) for dt in dates]
    adv = tr = comp = 0
    steps = []
    for i in range(1, len(dates)):
        a, b = d[i - 1], d[i]
        book_a = sum(a[x] for x in PEND) + a["completed"]
        book_b = sum(b[x] for x in PEND) + b["completed"]
        t = (book_b - book_a) - b["intake"]
        c = b["completed"] - a["completed"]
        ca, cb = sm.cumulative(b), sm.cumulative(a)
        ad = sum(max(ca[S[j]] - cb[S[j]], 0) for j in range(1, len(S)))
        adv += ad
        tr += t
        comp += c
        steps.append({"from": dates[i - 1], "to": dates[i], "transfers": t,
                      "completed": c, "advances": ad, "intake": b["intake"]})
    return {"pending": sum(d[-1][x] for x in PEND), "advances": adv,
            "transfers": tr, "completed": comp, "days": d, "steps": steps}


def build(circle: str, dates: list[str]):
    day = load_days(dates)
    names = sorted({k[0] for dt in dates for k in day[dt]})

    meta_path = os.path.join(ROOT, "public", "data.json")
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    members = [d["name"] for d in meta["divisions"] if d.get("circle") == circle]
    members = [m for m in members if m in names]

    def legal(k):
        return k[0] == "Araṇya bhavana" and "legal" in k[1].lower()

    parts = []
    for nm in members:
        m = measure(day, dates, lambda k, n=nm: k[0] == n)
        if m["pending"] or m["completed"] or m["advances"] or m["transfers"]:
            parts.append({"name": nm, **m})
    parts.sort(key=lambda r: -r["pending"])

    grp = measure(day, dates, lambda k: k[0] in members)
    lgl = measure(day, dates, legal)
    both = measure(day, dates, lambda k: k[0] in members or legal(k))

    return {"circle": circle, "dates": dates, "parts": parts,
            "group": grp, "legal": lgl, "both": both}


# ----------------------------------------------------------------- render

CSS = """
:root{--bg:#f6f8fb;--line:#dbe2ec;--soft:#eef2f7;--ink:#101d33;--ink2:#4d5c73;--mut:#8b98ab;
--green:#116b3c;--red:#b3241f;--amber:#9a6206;--blue:#1d4ed8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:28px 30px 70px}
h1{font-size:21px;margin:0 0 2px}
.sub{color:var(--ink2);margin:0 0 22px;font-size:13px}
h2{font-size:15.5px;margin:30px 0 8px}
p.note{font-size:13px;color:var(--ink2);margin:0 0 14px;max-width:88ch}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin-bottom:14px}
.big{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:8px}
@media(max-width:820px){.big{grid-template-columns:repeat(2,1fr)}}
.b{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.b .n{font-size:29px;font-weight:700;line-height:1.05;font-variant-numeric:tabular-nums}
.b .l{font-size:12.5px;color:var(--ink2);font-weight:600;margin-top:3px}
.b .h{font-size:11.5px;color:var(--mut);margin-top:2px}
.b.g .n{color:var(--green)}.b.r .n{color:var(--red)}.b.a .n{color:var(--amber)}.b.u .n{color:var(--blue)}
table{width:100%;border-collapse:collapse;font-size:13px;background:#fff}
th{background:#f2f5fa;padding:9px 10px;text-align:right;font-size:11px;font-weight:700;color:var(--ink2);
text-transform:uppercase;letter-spacing:.03em;border-bottom:1px solid var(--line);white-space:nowrap}
th.l{text-align:left}
td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--soft);font-variant-numeric:tabular-nums}
td.l{text-align:left}
tr.tot td{background:#f2f5fa;font-weight:700;border-top:2px solid var(--line)}
tr.whole td{background:#eaf7ef;font-weight:700}
tr.legal td{background:#fef6e6}
.sheet{border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:8px}
.pos{color:var(--green);font-weight:700}.neg{color:var(--red);font-weight:700}.z{color:#c3ccd8}
.flow{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:14px}
.arrow{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:10px 0}
.node{border:1px solid var(--line);border-radius:9px;padding:9px 14px;min-width:190px}
.node .t{font-weight:650;font-size:13.5px}
.node .v{font-size:12px;color:var(--ink2);margin-top:2px}
.node.out{border-color:#f0c4c2;background:#fdeceb}
.node.in{border-color:#f0dcae;background:#fef6e6}
.arw{font-size:20px;color:var(--mut)}
.grid3{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.u{background:#fff;border:1px solid var(--line);border-radius:11px;padding:13px 15px}
.u h3{margin:0 0 8px;font-size:13.5px}
.u table{font-size:12px}
.u td,.u th{padding:5px 6px}
.tag{font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:10px;margin-left:6px}
.tag.work{background:#eaf7ef;color:var(--green)}
.tag.tr{background:#fdeceb;color:var(--red)}
.tag.idle{background:var(--soft);color:var(--mut)}
"""


def num(v, invert=False):
    if v == 0:
        return '<span class="z">0</span>'
    cls = "pos" if (v > 0) != invert else "neg"
    return f'<span class="{cls}">{v:+d}</span>'


def render(d):
    dates, parts, g, l, b = d["dates"], d["parts"], d["group"], d["legal"], d["both"]
    sum_adv = sum(p["advances"] for p in parts)
    inner = sum_adv - g["advances"]
    cross = g["advances"] + l["advances"] - b["advances"]

    rows = ""
    for p in parts:
        did = p["completed"] > 0 or p["advances"] > 0
        tag = ('<span class="tag idle">never moved</span>' if not did and not p["transfers"]
               else ('<span class="tag work">finished cases</span>' if p["completed"] else ""))
        rows += (f'<tr><td class="l">{html.escape(p["name"])}{tag}</td>'
                 f'<td>{p["pending"]}</td><td>{p["advances"] or "<span class=z>0</span>"}</td>'
                 f'<td>{num(p["transfers"])}</td>'
                 f'<td>{p["completed"] or "<span class=z>0</span>"}</td></tr>')

    unit_cards = ""
    for p in parts:
        head = "".join(f"<th>{SHORT[x][:9]}</th>" for x in S)
        body = ""
        for i, dt in enumerate(dates):
            v = p["days"][i]
            body += (f'<tr><td class="l">{dt[5:]}</td>'
                     + "".join(f"<td>{v[x] or '<span class=z>0</span>'}</td>" for x in S)
                     + f'<td><b>{sum(v[x] for x in PEND)}</b></td></tr>')
        unit_cards += (f'<div class="u"><h3>{html.escape(p["name"])}</h3>'
                       f'<table><thead><tr><th class="l">Day</th>{head}<th>Pending</th></tr></thead>'
                       f'<tbody>{body}</tbody></table></div>')

    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(d['circle'])} — how the numbers move</title><style>{CSS}</style>
<div class="wrap">
<h1>{html.escape(d['circle'])} — how the numbers actually move</h1>
<p class="sub">{dates[0]} to {dates[-1]} · circle office, its divisions, and the HQ Legal Cell · all courts and case types</p>

<div class="big">
  <div class="b u"><div class="n">{sum_adv}</div><div class="l">Apparent advances</div>
    <div class="h">counting each office on its own</div></div>
  <div class="b a"><div class="n">{b['advances']}</div><div class="l">Left after combining</div>
    <div class="h">once internal transfers cancel</div></div>
  <div class="b r"><div class="n">{sum_adv + l['advances'] - b['advances']}</div><div class="l">Were transfers</div>
    <div class="h">files changing hands, not work</div></div>
  <div class="b g"><div class="n">{b['completed']}</div><div class="l">Cases actually finished</div>
    <div class="h">the only unambiguous output</div></div>
</div>

<h2>Each office on its own, then the same offices combined</h2>
<p class="note">A case that moves from one office to another is recorded twice: as an advance by the
office receiving it and as a reversal by the office losing it. Adding offices up separately keeps the
flattering half. Rolling them into the circle cancels the movement, because the case never left the
circle. The difference between the two rows is the size of the double count.</p>
<div class="sheet"><table>
<thead><tr><th class="l">Office</th><th>Pending</th><th>Apparent advances</th>
<th>Files changing hands</th><th>Cases finished</th></tr></thead>
<tbody>{rows}
<tr class="tot"><td class="l">Sum of the parts</td><td>{g['pending']}</td><td>{sum_adv}</td>
<td>{num(sum(p['transfers'] for p in parts))}</td><td>{sum(p['completed'] for p in parts)}</td></tr>
<tr class="whole"><td class="l">{html.escape(d['circle'])} treated as one office</td><td>{g['pending']}</td>
<td>{g['advances']}</td><td>{num(g['transfers'])}</td><td>{g['completed']}</td></tr>
<tr class="legal"><td class="l">HQ Legal Cell (for comparison)</td><td>{l['pending']}</td>
<td>{l['advances']}</td><td>{num(l['transfers'])}</td><td>{l['completed']}</td></tr>
<tr class="whole"><td class="l">Circle + Legal Cell combined</td><td>{b['pending']}</td>
<td>{b['advances']}</td><td>{num(b['transfers'])}</td><td>{b['completed']}</td></tr>
</tbody></table></div>
<p class="note"><b>{inner}</b> of the circle's apparent advances were divisions handing files to each
other. A further <b>{cross}</b> disappear when the Legal Cell is added, because those were files moving
between the circle and headquarters. Of {sum_adv + l['advances']} apparent advances across all these
offices counted separately, <b>{b['advances']}</b> survive — and only <b>{b['completed']}</b> cases
were actually finished.</p>

<h2>The 12 August transfer, traced</h2>
<div class="flow">
<p class="note" style="margin-bottom:4px">Statewide compliance stock was 1,193 cases on 11 August and
1,193 on 12 August — identical. Nobody worked any of these files. They changed desks.</p>
<div class="arrow">
  <div class="node out"><div class="t">Chikkaballapura Division</div>
    <div class="v">compliance 16 &rarr; 6 &nbsp;·&nbsp; <b>−10</b> &nbsp;·&nbsp; 0 completed</div></div>
  <div class="arw">&rarr;</div>
  <div class="node in"><div class="t">HQ Legal Cell, Addl PCCF</div>
    <div class="v">compliance 27 &rarr; 38 &nbsp;·&nbsp; <b>+11</b> &nbsp;·&nbsp; 0 completed</div></div>
</div>
<div class="arrow">
  <div class="node out"><div class="t">Bengaluru Circle office</div>
    <div class="v">compliance 2 &rarr; 1 &nbsp;·&nbsp; <b>−1</b></div></div>
  <div class="arw">&rarr;</div>
  <div class="node in"><div class="t">same desk</div><div class="v">10 + 1 = 11</div></div>
</div>
<p class="note" style="margin:8px 0 0">This single event is what made the HQ Legal Cell Addl PCCF the
department's top-scoring officer, and what made Chikkaballapura look like it was going backwards.</p>
</div>

<h2>What the circle genuinely did over the three days</h2>
<div class="card">
<table><thead><tr><th class="l">Stage</th>{"".join(f"<th>{dt[5:]}</th>" for dt in dates)}<th>Change</th></tr></thead>
<tbody>{"".join(
    f'<tr><td class="l">{SHORT[x]}</td>'
    + "".join(f"<td>{g['days'][i][x]}</td>" for i in range(len(dates)))
    + f"<td>{num(g['days'][-1][x] - g['days'][0][x], invert=(x != 'completed'))}</td></tr>"
    for x in S)}</tbody></table>
<p class="note" style="margin:12px 0 0">Read down the change column: <b>9 files picked up</b> out of No
Action, <b>6 brought to hearing</b>, <b>10 cases finished</b>. Compliance fell by 13, but 11 of that was
the transfer to headquarters. The LCO proposal stage <i>grew</i> — more proposals arrived than left.</p>
</div>

<h2>Every office, day by day</h2>
<div class="grid3">{unit_cards}</div>

<h2>How to read this</h2>
<p class="note">
<b>Book total = pending + completed.</b> A case can only leave an office's book total by being handed to
another office, so the change in it measures files moving, not work done. That is the test that caught
the 12 August transfer.<br><br>
<b>Rank at circle level.</b> Inside a circle, transfers between divisions cancel out, so the circle total
is honest. Division and officer figures are worth asking questions about, but they are not scores.<br><br>
<b>A completion is the only number that cannot be faked by movement.</b> When the two disagree, trust the
completion count.</p>
</div></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--circle", default="Bengaluru Circle")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    dates = an.available_dates()
    d = build(args.circle, dates)
    slug = args.circle.lower().replace(" ", "_")
    out = args.out or os.path.join(ROOT, "public", f"circle_{slug}.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(d))
    sa = sum(p["advances"] for p in d["parts"])
    print(f"{args.circle}: parts={sa} advances, combined with Legal Cell={d['both']['advances']}, "
          f"finished={d['both']['completed']}")
    print("written:", out)


if __name__ == "__main__":
    main()
