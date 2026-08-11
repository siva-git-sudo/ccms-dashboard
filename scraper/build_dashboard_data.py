#!/usr/bin/env python3
"""
Reads every data/snapshots/YYYY-MM-DD.json produced by scrape_ccms.py,
compares the latest snapshot to the one before it, and writes
public/data.json for the dashboard: one entry per division with today's
total pending cases, the change vs. the previous snapshot, and a
direction ("increase" / "decrease" / "same") the frontend uses to pick a
green or red arrow.

Can also be run standalone: `python build_dashboard_data.py`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from parse_ccms_xml import columns_for_layout, COLUMN_LABELS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
PUBLIC_DIR = ROOT / "public"

IST = timezone(timedelta(hours=5, minutes=30))


OTHER_CIRCLE = "Other / direct reporting"


def load_divisions() -> list[dict]:
    return json.loads((HERE / "divisions.json").read_text(encoding="utf-8"))


def load_circle_index() -> tuple[dict, list[str], set[str]]:
    """Return (normalised division name -> circle, circle names, excluded).

    circles.json maps each circle to its divisions using the exact CCMS
    department names. Anything the reports return that is not listed --
    boards, corporations, zoos, tiger reserves that report directly --
    falls into OTHER_CIRCLE rather than disappearing from the dashboard.

    That fallback is deliberate, which is why hiding a department needs
    an explicit `_exclude` entry: simply deleting it from a circle would
    reroute it to OTHER_CIRCLE, not remove it.

    `_exclude` applies here, at build time, and nowhere else. The scraper
    still fetches those departments and the snapshots still record them,
    so the history is intact -- removing a name from `_exclude` brings
    its whole past back on the next build, with no re-scraping.
    """
    path = HERE / "circles.json"
    if not path.exists():
        return {}, [], set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, [], set()

    index, order = {}, []
    for circle, cfg in raw.items():
        if circle.startswith("_"):
            continue
        order.append(circle)
        for name in (cfg or {}).get("divisions", []):
            index[_norm(name)] = circle
    excluded = {_norm(n) for n in raw.get("_exclude") or []}
    return index, order, excluded


def _norm(name: str) -> str:
    return " ".join((name or "").split()).lower()


UNASSIGNED_WING = "Unassigned"


def load_wing_index() -> tuple[dict, list[str], set[str]]:
    """Return (normalised section -> wing, ordered wing names, HQ dept names).

    wings.json groups the CCMS "user" rows of the Aranya Bhavana
    departments into the wings/units they actually sit under. The report
    gives us a free-text `section` per user and nothing above it, so the
    wing has to come from this hand-maintained map -- same approach as
    circles.json for divisions.

    The map applies only to the departments in `applies_to_departments`;
    a section name like "Legal Cell" in a field division is that
    division's own cell, not the HQ wing, so it is left unwinged rather
    than folded into an HQ total.

    `_unmapped` groups (field offices created under the HQ code, system
    accounts) are carried through as wings too, under their `label`, so
    they stay visible instead of silently inflating a real wing.
    """
    path = HERE / "wings.json"
    if not path.exists():
        return {}, [], set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, [], set()

    index, order = {}, []

    def add(label: str, cfg: dict) -> None:
        if label not in order:
            order.append(label)
        for section in (cfg or {}).get("sections", []):
            index[_norm(section)] = label

    for name, cfg in (raw.get("wings") or {}).items():
        if name.startswith("_"):
            continue
        add(name, cfg)
    for key, cfg in (raw.get("_unmapped") or {}).items():
        if key.startswith("_"):
            continue
        add((cfg or {}).get("label") or key, cfg)

    depts = {_norm(d) for d in raw.get("applies_to_departments") or []}
    return index, order, depts


def _wing_of(section: str, wing_index: dict, in_scope: bool) -> str | None:
    """The wing a user row belongs to, or None if wings don't apply here."""
    if not in_scope:
        return None
    return wing_index.get(_norm(section), UNASSIGNED_WING)


def load_snapshots() -> list[dict]:
    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    snapshots = []
    for f in files:
        try:
            snapshots.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return snapshots


def _delta_and_direction(total, prev_total):
    if total is not None and prev_total is not None:
        delta = total - prev_total
        if delta > 0:
            return delta, "increase"
        if delta < 0:
            return delta, "decrease"
        return delta, "same"
    if total is not None:
        return None, "baseline"  # first snapshot we have
    return None, "no_data"


def _officers_with_deltas(
    latest_rows: list[dict],
    prev_rows: list[dict],
    wing_index: dict | None = None,
    winged: bool = False,
) -> list[dict]:
    prev_by_key = {(r["section"], r["post"]): r for r in (prev_rows or [])}
    out = []
    for row in latest_rows or []:
        key = (row["section"], row["post"])
        prev_row = prev_by_key.get(key)
        total = row.get("total_cases_pending")
        prev_total = prev_row.get("total_cases_pending") if prev_row else None
        delta, direction = _delta_and_direction(total, prev_total)
        out.append(
            {
                "section": row["section"],
                "post": row["post"],
                "wing": _wing_of(row["section"], wing_index or {}, winged),
                "total_pending": total,
                "previous_total_pending": prev_total,
                "delta": delta,
                "direction": direction,
            }
        )
    # Group by section, largest pending first within each section, for a
    # readable "user" list under each division card.
    out.sort(key=lambda r: (r["section"], -(r["total_pending"] or 0)))
    return out


def _wing_rollup(users: list[dict], wing_order: list[str]) -> list[dict]:
    """Collapse a unit's user rows into one row per wing.

    Only meaningful for the HQ departments -- everywhere else `wing` is
    None and this returns []. Wings are ordered by caseload, so the
    heaviest wing is the first thing read off the card.
    """
    buckets: dict[str, dict] = {}
    for u in users:
        wing = u.get("wing")
        if not wing:
            continue
        b = buckets.setdefault(
            wing, {"wing": wing, "total_pending": 0, "previous_total_pending": 0,
                   "user_count": 0, "_had_prev": False}
        )
        b["total_pending"] += u.get("total_pending") or 0
        if u.get("previous_total_pending") is not None:
            b["previous_total_pending"] += u["previous_total_pending"]
            b["_had_prev"] = True
        b["user_count"] += 1

    out = []
    for wing, b in buckets.items():
        prev = b["previous_total_pending"] if b["_had_prev"] else None
        delta, direction = _delta_and_direction(b["total_pending"], prev)
        out.append({
            "wing": wing,
            "total_pending": b["total_pending"],
            "previous_total_pending": prev,
            "user_count": b["user_count"],
            "delta": delta,
            "direction": direction,
            "order": wing_order.index(wing) if wing in wing_order else len(wing_order),
        })
    out.sort(key=lambda r: (-r["total_pending"], r["order"]))
    return out


def _write_outputs(payload: dict) -> None:
    """Write the dashboard data twice, in two formats.

    data.json -- fetched when the page is served over http(s), e.g. from
                 Firebase Hosting. Always the freshest source.
    data.js   -- the same payload assigned to window.CCMS_DATA, loaded via
                 a plain <script> tag.

    The second file exists because browsers block fetch() of local files
    under the file:// scheme (CORS), so opening public/index.html directly
    off disk would otherwise show "Could not load data.json". <script>
    tags are not subject to that restriction, so the dashboard works when
    double-clicked as well as when hosted.
    """
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    (PUBLIC_DIR / "data.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (PUBLIC_DIR / "data.js").write_text(
        "// Generated by build_dashboard_data.py -- do not edit.\n"
        "// Fallback copy of data.json so the dashboard also works when\n"
        "// index.html is opened directly from disk (file://).\n"
        "window.CCMS_DATA = "
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )


def build() -> dict:
    snapshots = load_snapshots()
    # Prefer the division list the scrape actually used -- in
    # CCMS_TRACK_ALL mode it is derived from the reports, not from
    # divisions.json.
    divisions = (snapshots[-1].get("divisions_meta") if snapshots else None) or load_divisions()

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    if not snapshots:
        payload = {
            "generated_at": datetime.now(IST).isoformat(),
            "latest_date": None,
            "previous_date": None,
            "divisions": [
                {
                    "code": d["code"].strip(),
                    "name": d["name"],
                    "group": d["group"],
                    "total_pending": None,
                    "previous_total_pending": None,
                    "delta": None,
                    "direction": "no_data",
                    "users": [],
                }
                for d in divisions
            ],
        }
        _write_outputs(payload)
        return payload

    latest = snapshots[-1]
    previous = snapshots[-2] if len(snapshots) >= 2 else None
    circle_index, circle_order, excluded = load_circle_index()
    wing_index, wing_order, wing_depts = load_wing_index()
    divisions = [d for d in divisions if _norm(d["name"]) not in excluded]

    out_divisions = []
    for d in divisions:
        code, name, group = d["code"], d["name"], d["group"]
        winged = _norm(name) in wing_depts
        latest_rec = latest.get("divisions", {}).get(code)
        prev_rec = previous.get("divisions", {}).get(code) if previous else None

        total = latest_rec.get("total_cases_pending") if latest_rec else None
        prev_total = prev_rec.get("total_cases_pending") if prev_rec else None
        delta, direction = _delta_and_direction(total, prev_total)

        latest_officers = latest.get("officers", {}).get(code, [])
        prev_officers = previous.get("officers", {}).get(code, []) if previous else []
        users = _officers_with_deltas(latest_officers, prev_officers, wing_index, winged)

        out_divisions.append(
            {
                "code": code.strip(),
                "name": name,
                "group": group,
                "circle": circle_index.get(_norm(name), OTHER_CIRCLE),
                "total_pending": total,
                "previous_total_pending": prev_total,
                "delta": delta,
                "direction": direction,
                "metrics": {
                    k: v
                    for k, v in (latest_rec or {}).items()
                    if k not in ("raw", "footer_tag", "_combined_from", "_skipped", "combined_from_combos")
                },
                "users": users,
                "wings": _wing_rollup(users, wing_order),
            }
        )

    payload = {
        "generated_at": datetime.now(IST).isoformat(),
        "latest_date": latest.get("date"),
        "previous_date": previous.get("date") if previous else None,
        "divisions": out_divisions,
        "circles": circle_order + [OTHER_CIRCLE],
        "wings": wing_order,
        "case_types": _build_case_types(latest, previous, divisions),
    }

    _write_outputs(payload)
    return payload


def _metrics_of(payload: dict) -> dict:
    """Pull the numeric columns out of a parsed row, dropping bookkeeping keys."""
    if not payload:
        return {}
    src = payload.get("totals", payload)
    return {
        k: v
        for k, v in src.items()
        if isinstance(v, (int, float)) and not k.startswith("_")
    }


_SUB_MARKER = re.compile(r"^\(\s*[ivx]+\s*\)$", re.I)   # (i) (ii) (iii)
_PLAIN_NUM = re.compile(r"^\d+$")
_GROUP_HEADER = re.compile(r"^\d+\.?\s+\S")             # "11. Appealed", "14 Completed Case"


def _flatten_report_headers(main: list[str], following: list[list[str]] | None) -> list[str] | None:
    """Turn a report's header rows into one flat heading per column.

    Some reports group columns under a spanning header whose sub-columns
    are named on the row below. Civil Contempt is the clear case: its
    main row has 16 data headings for 20 columns, because "11. Appealed"
    and "15. Completed Case" each span three sub-columns.

    The row of column numbers disambiguates it exactly -- plain numbers
    mark ordinary columns, roman markers mark sub-columns of the group
    that precedes them:

        1 2 3 4 5 6 7 8 9 10 (i) (ii) (iii) 12 13 14 (i) (ii) (iii)
                              \\__ 11. Appealed __/     \\_ 15. Completed _/

    Returns one heading per numeric column, or None if the rows don't
    provide enough information to map them safely.
    """
    if not main or not following:
        return None

    # Data headings start at the first "received"/"pending" heading --
    # everything before that is a row-label column.
    start = next(
        (i for i, h in enumerate(main)
         if re.search(r"received as on|^received|total cases pending", h, re.I)),
        None,
    )
    if start is None:
        return None
    data_main = main[start:]

    # Among the following rows, find the numbering row and the sub-header row.
    numbering = None
    subs = None
    for row in following:
        toks = [t.strip() for t in row if t and t.strip()]
        if not toks:
            continue
        if numbering is None and sum(
            1 for t in toks if _PLAIN_NUM.match(t) or _SUB_MARKER.match(t)
        ) >= max(3, len(toks) * 0.8):
            numbering = toks
        elif subs is None and all(re.search(r"[A-Za-z]{3,}", t) for t in toks):
            subs = toks
    if not numbering:
        return None
    if any(_SUB_MARKER.match(t) for t in numbering) and not subs:
        return None  # grouped report but no sub-headings -- cannot map

    out = [data_main[0]]          # first column is unnumbered ("Received ...")
    mi, si, in_group = 1, 0, False
    for tok in numbering:
        if _SUB_MARKER.match(tok):
            if not in_group:
                mi += 1           # step over the group heading itself
                in_group = True
            if si >= len(subs or []):
                return None
            out.append(subs[si]); si += 1
        else:
            in_group = False
            if mi >= len(data_main):
                return None
            out.append(data_main[mi]); mi += 1

    return out


def _apply_scraped_headers(columns: list[dict], scraped: list[str] | None) -> list[dict]:
    """Overlay column headings scraped from the rendered report.

    The scraped row usually starts with the label columns ("Secretariat
    Department", "Department Names", ...) before the numeric ones, so the
    numeric headings are taken from the END of the list -- the last
    len(columns) entries line up with the numeric columns right-to-left.
    Only applied when there are enough headings to cover every column;
    otherwise the existing labels are kept rather than risking a
    misaligned mapping.
    """
    if not scraped or not columns:
        return columns

    tail = [h for h in scraped if h and h.strip()]
    if len(tail) < len(columns):
        return columns

    tail = tail[-len(columns):]
    out = []
    for col, header in zip(columns, tail):
        if col.get("known"):
            out.append(col)  # confirmed headings win
        else:
            out.append({"key": col["key"], "header": header, "known": True, "scraped": True})
    return out


def _build_case_types(latest: dict, previous: dict | None, divisions: list[dict]) -> list[dict]:
    """Build one table per case type, each with the columns that report
    actually has -- rather than merging reports whose column layouts
    differ (Writ Petition has 17 columns, Civil Contempt 20, Writ Appeal
    and S-KSAT 16). Each row carries its full metric set plus a delta on
    Total Cases Pending versus the previous snapshot.
    """
    circle_index, _, excluded = load_circle_index()
    wing_index, _wing_order, wing_depts = load_wing_index()
    divisions = [d for d in divisions if _norm(d["name"]) not in excluded]
    meta = latest.get("case_types") or []
    latest_by_ct = latest.get("by_case_type") or {}
    prev_by_ct = (previous or {}).get("by_case_type") or {}

    # Column headings live in their own file (written by
    # `scrape_ccms.py --headers`) so that rebuilding a snapshot can never
    # lose them -- they change only when the report definition changes.
    header_store = {}
    hp = ROOT / "data" / "report_headers.json"
    if hp.exists():
        try:
            header_store = json.loads(hp.read_text(encoding="utf-8"))
        except Exception:
            header_store = {}

    out = []
    for m in meta:
        key = m["key"]
        latest_rows = latest_by_ct.get(key) or {}
        prev_rows = prev_by_ct.get(key) or {}

        # A report with no rows is still worth showing -- it means "we
        # checked this case type and your divisions have none", which is
        # different from "we never looked". Only skip it if the report
        # failed to scrape at all.
        scraped_ok = key in (latest.get("seen_department_names_by_combo") or {})
        if not latest_rows and not scraped_ok:
            continue

        # Column layout is whatever this report actually produced.
        n_cols = 0
        for payload in latest_rows.values():
            n_cols = max(n_cols, (payload.get("totals") or {}).get("_column_count") or 0)
        # An empty report tells us nothing about its layout; assume the
        # standard one so the table still renders with proper headings.
        columns = columns_for_layout(n_cols or len(COLUMN_LABELS))

        # Prefer the headings scraped off the rendered report -- the XML
        # export omits them, so without this any report whose layout is
        # not the 17-column Writ Petition one would show "Column N".
        hs = header_store.get(key) or (latest.get("report_headers") or {}).get(key) or {}
        flat = _flatten_report_headers(hs.get("headers"), hs.get("followingRows"))
        columns = _apply_scraped_headers(columns, flat or hs.get("headers"))

        rows = []
        for d in divisions:
            code = d["code"]
            payload = latest_rows.get(code)
            # Every tracked unit appears in every report, even with no
            # cases. A report only lists units that HAVE cases, so an
            # absent unit means a genuine zero -- and "0" is useful
            # information (it says the unit is clean, not missing).
            if payload is None:
                if not scraped_ok:
                    continue  # report failed; we do not know, so say nothing
                metrics = {c["key"]: 0 for c in columns}
                payload = {"officers": []}
            else:
                metrics = _metrics_of(payload)
            prev_metrics = _metrics_of(prev_rows.get(code))

            total = metrics.get("total_cases_pending")
            prev_total = prev_metrics.get("total_cases_pending")
            delta, direction = _delta_and_direction(total, prev_total)

            users = []
            prev_users = {
                (u.get("section"), u.get("post")): u
                for u in (prev_rows.get(code, {}) or {}).get("officers", [])
            }
            for u in payload.get("officers", []):
                u_metrics = {
                    k: v for k, v in u.items()
                    if isinstance(v, (int, float)) and not k.startswith("_")
                }
                pu = prev_users.get((u.get("section"), u.get("post")), {})
                u_delta, u_direction = _delta_and_direction(
                    u_metrics.get("total_cases_pending"),
                    pu.get("total_cases_pending"),
                )
                users.append({
                    "post": u.get("post", ""),
                    "section": u.get("section", ""),
                    "wing": _wing_of(
                        u.get("section", ""), wing_index, _norm(d["name"]) in wing_depts
                    ),
                    "metrics": u_metrics,
                    "delta": u_delta,
                    "direction": u_direction,
                })
            users.sort(key=lambda r: -(r["metrics"].get("total_cases_pending") or 0))

            rows.append({
                "code": code.strip(),
                "name": d["name"],
                "group": d["group"],
                "circle": circle_index.get(_norm(d["name"]), OTHER_CIRCLE),
                "metrics": metrics,
                "delta": delta,
                "direction": direction,
                "users": users,
            })

        rows.sort(key=lambda r: -(r["metrics"].get("total_cases_pending") or 0))

        totals = {}
        for c in columns:
            vals = [r["metrics"].get(c["key"]) for r in rows]
            vals = [v for v in vals if isinstance(v, (int, float))]
            totals[c["key"]] = sum(vals) if vals else 0

        out.append({
            "key": key,
            "label": m["label"],
            "court": m["court"],
            "columns": columns,
            "column_count": n_cols,
            "all_columns_known": all(c["known"] for c in columns),
            "rows": rows,
            "totals": totals,
        })

    return out


if __name__ == "__main__":
    result = build()
    print(json.dumps(result, indent=2, ensure_ascii=False))
