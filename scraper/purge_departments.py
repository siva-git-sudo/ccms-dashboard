#!/usr/bin/env python3
"""Erase excluded departments from the stored history, then rebuild.

`_exclude` in circles.json keeps a department out of the dashboard, but
the snapshots under data/snapshots/ are written by the scraper before any
of that applies, so an excluded department stays in the stored history
until it is removed. This script does that removal: it rewrites every
snapshot with the excluded departments stripped out of every structure
they appear in, then regenerates public/data.json and public/data.js.

Run it once after adding a name to `_exclude`:

    python3 scraper/purge_departments.py            # show what would go
    python3 scraper/purge_departments.py --apply    # actually do it

Nothing is guessed: a department is matched on the same normalised name
the rest of the pipeline uses (case-insensitive, whitespace collapsed),
so the CCMS double space in "UTTARA KANNADA DIVISION  ENVIRONMENT" is
handled without having to reproduce it exactly.

This edits history in place. `--apply` writes a .bak beside each snapshot
it touches, so a mistaken exclusion can be undone.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from build_dashboard_data import HERE, ROOT, SNAPSHOT_DIR, _norm, load_circle_index


def codes_to_drop(snapshot: dict, excluded: set[str]) -> dict[str, str]:
    """Department codes in this snapshot whose name is excluded.

    Codes are read from divisions_meta rather than assumed, because they
    are assigned by CCMS and can differ between scrapes.
    """
    out = {}
    for d in snapshot.get("divisions_meta") or []:
        if _norm(d.get("name", "")) in excluded:
            out[d.get("code", "").strip()] = d.get("name", "")
    # Fall back to the divisions block for older snapshots that predate
    # divisions_meta.
    for code, rec in (snapshot.get("divisions") or {}).items():
        name = (rec or {}).get("department_name") or (rec or {}).get("name") or ""
        if name and _norm(name) in excluded:
            out.setdefault(code.strip(), name)
    return out


def purge(snapshot: dict, excluded: set[str]) -> tuple[dict, dict[str, str]]:
    """Return (cleaned snapshot, {code: name} removed)."""
    dropped = codes_to_drop(snapshot, excluded)
    codes = {c for c in dropped}

    def drop_keys(mapping):
        if not isinstance(mapping, dict):
            return mapping
        return {k: v for k, v in mapping.items() if k.strip() not in codes}

    snapshot["divisions"] = drop_keys(snapshot.get("divisions"))
    snapshot["officers"] = drop_keys(snapshot.get("officers"))

    by_ct = snapshot.get("by_case_type") or {}
    snapshot["by_case_type"] = {k: drop_keys(v) for k, v in by_ct.items()}

    snapshot["divisions_meta"] = [
        d for d in (snapshot.get("divisions_meta") or [])
        if _norm(d.get("name", "")) not in excluded
    ]

    # The audit trail of what each report returned -- drop the names so a
    # purged department cannot reappear through the "did we see it?" check.
    seen = snapshot.get("seen_department_names_by_combo") or {}
    snapshot["seen_department_names_by_combo"] = {
        combo: [n for n in names if _norm(n) not in excluded]
        for combo, names in seen.items()
    }

    return snapshot, dropped


def main(apply: bool) -> int:
    _, _, excluded = load_circle_index()
    if not excluded:
        print("No `_exclude` list in circles.json — nothing to purge.")
        return 0

    print("Excluded departments:")
    for name in sorted(excluded):
        print(f"  - {name}")
    print()

    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not files:
        print(f"No snapshots found in {SNAPSHOT_DIR}.")
        return 1

    touched = 0
    for f in files:
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"{f.name}: unreadable, skipped")
            continue

        before = len(snap.get("divisions") or {})
        cleaned, dropped = purge(snap, excluded)
        if not dropped:
            print(f"{f.name}: clean")
            continue

        touched += 1
        after = len(cleaned.get("divisions") or {})
        detail = ", ".join(f"{c} {n}" for c, n in sorted(dropped.items()))
        print(f"{f.name}: removing {len(dropped)} ({detail}) — {before} → {after} departments")

        if apply:
            shutil.copy2(f, f.with_suffix(".json.bak"))
            f.write_text(
                json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    print()
    if not apply:
        print("Dry run — nothing written. Re-run with --apply to make the change.")
        return 0

    print(f"Rewrote {touched} snapshot(s); .bak copies kept alongside them.")
    from build_dashboard_data import build

    payload = build()
    print(f"Rebuilt public/data.json — {len(payload.get('divisions', []))} departments.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    raise SystemExit(main("--apply" in sys.argv))
