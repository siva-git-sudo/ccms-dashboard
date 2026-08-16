#!/usr/bin/env python3
"""
Rebuild snapshots from data/raw/<date>/*.xml using the updated parse_ccms_xml.py
"""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

sys.path.insert(0, str(HERE))
from parse_ccms_xml import (
    parse_ccms_xml_by_division,
    parse_ccms_xml_officers,
    sum_parsed,
    sum_officer_rows,
    normalize_name,
    columns_for_layout
)

COURT_CASE_TYPE_COMBOS = [
    {"court_id": "1", "case_type": "CCC", "label": "Civil Contempt Petition", "filename": "all_departments__court1_CCC.xml"},
    {"court_id": "1", "case_type": "WA", "label": "Writ Appeal", "filename": "all_departments__court1_WA.xml"},
    {"court_id": "1", "case_type": "WP", "label": "Writ Petition", "filename": "all_departments__court1_WP.xml"},
    {"court_id": "1", "case_type": "S-KSAT", "label": "Special KSAT Cases (HC)", "filename": "all_departments__court1_S-KSAT.xml"},
    {"court_id": "3", "case_type": "CA", "label": "Contempt Application", "filename": "all_departments__court3_CA.xml"},
    {"court_id": "3", "case_type": "MA", "label": "Miscellaneous Application", "filename": "all_departments__court3_MA.xml"},
    {"court_id": "3", "case_type": "OA ", "label": "Original Application", "filename": "all_departments__court3_OA.xml"},
    {"court_id": "3", "case_type": "RA", "label": "Review Application", "filename": "all_departments__court3_RA.xml"},
]

def load_divisions():
    with open(HERE / "divisions.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("divisions", [])
    return data

def rebuild_snapshot(date_str: str):
    raw_date_dir = RAW_DIR / date_str
    if not raw_date_dir.exists():
        print(f"Directory {raw_date_dir} does not exist, skipping.")
        return

    divisions = load_divisions()
    by_normalized = {normalize_name(d["name"]): d for d in divisions}

    per_division_combo_results = {d["code"]: [] for d in divisions}
    by_case_type = {}
    combo_columns = {}

    for combo in COURT_CASE_TYPE_COMBOS:
        label = combo["label"]
        xml_file = raw_date_dir / combo["filename"]
        if not xml_file.exists():
            print(f"  Missing {xml_file}")
            continue

        try:
            by_division = parse_ccms_xml_by_division(xml_file)
        except Exception as e:
            print(f"  Error parsing {xml_file}: {e}")
            continue

        for div_name, div_payload in by_division.items():
            norm = normalize_name(div_name)
            target = by_normalized.get(norm)
            if not target:
                continue
            
            payload_totals = div_payload.get("totals", {})
            div_officers = div_payload.get("officers", [])
            
            per_division_combo_results[target["code"]].append({
                "combo": label,
                "totals": payload_totals,
                "officers": div_officers,
            })
            by_case_type.setdefault(label, {})[target["code"]] = payload_totals

            if label not in combo_columns:
                n_cols = payload_totals.get("_column_count", 17)
                combo_columns[label] = {
                    "column_count": n_cols,
                    "columns": columns_for_layout(n_cols),
                }

    results = {}
    officers_by_code = {}
    for d in divisions:
        code = d["code"]
        combo_results = per_division_combo_results[code]
        totals_list = [r["totals"] for r in combo_results]
        officers_lists = [r["officers"] for r in combo_results]

        combined = sum_parsed(totals_list)
        combined["combined_from_combos"] = len(totals_list)
        results[code] = combined
        officers_by_code[code] = sum_officer_rows(officers_lists)

    snapshot = {
        "date": date_str,
        "results": results,
        "officers": officers_by_code,
        "by_case_type": by_case_type,
        "case_type_columns": combo_columns,
    }

    out_file = SNAPSHOT_DIR / f"{date_str}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    print(f"Successfully rebuilt {out_file}")

def main():
    dates = ["2026-08-11", "2026-08-12", "2026-08-13"]
    for dt in dates:
        rebuild_snapshot(dt)

if __name__ == "__main__":
    main()
