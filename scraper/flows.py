#!/usr/bin/env python3
"""
flows.py — Reconstruct Case Handovers & Desk-to-Desk Transfers from CCMS Snapshots.

Problem:
--------
CCMS report snapshots capture stocks per officer login, not transaction logs with case IDs.
When a case changes hands from one officer to another, it leaves one officer's stock (loss)
and appears on another officer's stock (gain) at the EXACT SAME STAGE.

Algorithm:
----------
For each stage s and between two snapshots (t1 -> t2):
1. Identify all sending desks (stock at stage s decreased: loss = -delta > 0).
2. Identify all receiving desks (stock at stage s increased: gain = delta > 0).
3. Match senders to receivers with strict Locality Preference:
   - Priority 1: Same Division (Exact departmental transfer — High Confidence)
   - Priority 2: Same Circle (Territorial handover — Medium Confidence)
   - Priority 3: Cross-Department / Field <-> HQ (Possible / Candidate match)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STAGE_KEYS = [
    "no_action_taken",
    "lco_proposal_stage_pending",
    "ga_lco_authorization_pending",
    "draft_pwr_pending",
    "approved_pwr_pending",
    "draft_so_pending_from_advocate",
    "approved_so_pending",
    "affidavit_filing_pending",
    "affidavit_filed_hearing_stage",
    "interim_order_compliance_pending",
    "disposed_case",
    "final_order_compliance_pending",
    "proposed_for_appeal",
    "closed_with_appeal_number",
    "not_to_appeal",
]

STAGE_NAMES = {
    "no_action_taken": "No Action",
    "lco_proposal_stage_pending": "LCO Proposal",
    "ga_lco_authorization_pending": "GA Authorization",
    "draft_pwr_pending": "Draft PWR",
    "approved_pwr_pending": "Approved PWR",
    "draft_so_pending_from_advocate": "Draft SO (Advocate)",
    "approved_so_pending": "Approved SO",
    "affidavit_filing_pending": "Affidavit Filing",
    "affidavit_filed_hearing_stage": "In HC Hearing",
    "interim_order_compliance_pending": "Interim Compliance",
    "disposed_case": "Disposed",
    "final_order_compliance_pending": "Final Order Compliance",
    "proposed_for_appeal": "Proposed for Appeal",
    "closed_with_appeal_number": "Closed (Appeal No)",
    "not_to_appeal": "Closed (Not to Appeal)",
}


def reconstruct_flows(
    curr_snapshot: dict[str, Any],
    prev_snapshot: dict[str, Any],
    min_count: int = 1
) -> dict[str, Any]:
    """
    Reconstruct officer-to-officer handovers between two snapshots.
    """
    curr_officers_by_div = curr_snapshot.get("officers", {})
    prev_officers_by_div = prev_snapshot.get("officers", {})
    
    # Flatten officer lists with division context
    curr_officers = []
    for div_code, o_list in curr_officers_by_div.items():
        for o in o_list:
            curr_officers.append({"div_code": div_code, **o})
            
    prev_officers = []
    for div_code, o_list in prev_officers_by_div.items():
        for o in o_list:
            prev_officers.append({"div_code": div_code, **o})
            
    # Index by (div_code, section, post)
    def officer_key(o: dict) -> tuple:
        return (o.get("div_code", ""), o.get("section", ""), o.get("post", ""))
        
    curr_map = {officer_key(o): o for o in curr_officers}
    prev_map = {officer_key(o): o for o in prev_officers}
    all_keys = set(curr_map.keys()) | set(prev_map.keys())
    
    handovers = []
    total_shuffled = 0
    
    for st in STAGE_KEYS:
        senders = []   # list of {"key": k, "div": div, "section": sec, "post": post, "loss": int}
        receivers = [] # list of {"key": k, "div": div, "section": sec, "post": post, "gain": int}
        
        for k in all_keys:
            c_val = int((curr_map.get(k) or {}).get(st) or 0)
            p_val = int((prev_map.get(k) or {}).get(st) or 0)
            diff = c_val - p_val
            
            if diff < 0:
                senders.append({
                    "div_code": k[0],
                    "section": k[1],
                    "post": k[2],
                    "loss": abs(diff),
                })
            elif diff > 0:
                receivers.append({
                    "div_code": k[0],
                    "section": k[1],
                    "post": k[2],
                    "gain": diff,
                })
                
        # Matching with Locality Preference
        # Level 1: Same division
        for s in senders:
            if s["loss"] <= 0:
                continue
            for r in receivers:
                if r["gain"] <= 0:
                    continue
                if s["div_code"] == r["div_code"] and (s["section"] != r["section"] or s["post"] != r["post"]):
                    matched = min(s["loss"], r["gain"])
                    if matched >= min_count:
                        handovers.append({
                            "stage_key": st,
                            "stage": STAGE_NAMES.get(st, st),
                            "from_div": s["div_code"],
                            "from_section": s["section"],
                            "from_post": s["post"],
                            "to_div": r["div_code"],
                            "to_section": r["section"],
                            "to_post": r["post"],
                            "count": matched,
                            "locality": "Same Division",
                            "confidence": "High",
                        })
                        total_shuffled += matched
                        s["loss"] -= matched
                        r["gain"] -= matched
                        if s["loss"] <= 0:
                            break

        # Level 2: Cross-Division (Field <-> HQ or Field <-> Field)
        for s in senders:
            if s["loss"] <= 0:
                continue
            for r in receivers:
                if r["gain"] <= 0:
                    continue
                matched = min(s["loss"], r["gain"])
                if matched >= min_count:
                    handovers.append({
                        "stage_key": st,
                        "stage": STAGE_NAMES.get(st, st),
                        "from_div": s["div_code"],
                        "from_section": s["section"],
                        "from_post": s["post"],
                        "to_div": r["div_code"],
                        "to_section": r["section"],
                        "to_post": r["post"],
                        "count": matched,
                        "locality": "Cross-Department",
                        "confidence": "Possible",
                    })
                    total_shuffled += matched
                    s["loss"] -= matched
                    r["gain"] -= matched
                    if s["loss"] <= 0:
                        break

    handovers.sort(key=lambda x: -x["count"])
    return {
        "date_from": prev_snapshot.get("date"),
        "date_to": curr_snapshot.get("date"),
        "total_cases_shuffled": total_shuffled,
        "handovers_count": len(handovers),
        "handovers": handovers,
    }


def main():
    import sys
    root = Path(__file__).resolve().parent.parent
    snap_dir = root / "data" / "snapshots"
    snaps = sorted(snap_dir.glob("*.json"))
    if len(snaps) < 2:
        print("Need at least 2 snapshots to compute flows.")
        return
        
    s1 = json.loads(snaps[0].read_text(encoding="utf-8"))
    s2 = json.loads(snaps[-1].read_text(encoding="utf-8"))
    
    res = reconstruct_flows(s2, s1, min_count=1)
    print(f"Handovers between {res['date_from']} and {res['date_to']}:")
    print(f"Total cases shuffled: {res['total_cases_shuffled']} across {res['handovers_count']} movements\n")
    # Load divisions to resolve names
    div_names = {}
    try:
        div_list = json.loads((root / "scraper" / "divisions.json").read_text(encoding="utf-8"))
        div_names = {d["code"]: d["name"] for d in div_list}
    except Exception:
        pass

    for h in res["handovers"][:15]:
        f_name = div_names.get(h['from_div'], h['from_div'])
        t_name = div_names.get(h['to_div'], h['to_div'])
        msg = f"  * {h['count']} cases at [{h['stage']}]: {h['from_post']} ({f_name}) -> {h['to_post']} ({t_name}) [{h['locality']} - {h['confidence']}]"
        print(msg.encode("ascii", "replace").decode("ascii"))

if __name__ == "__main__":
    main()
