#!/usr/bin/env python3
"""
scorecard.py — Intake-Proof Throughput & Cumulative Reverse Stock Performance Engine
for Karnataka Forest Department CCMS.

Mathematical Foundation:
------------------------
Snapshots capture stage stocks S(t) at discrete dates.
Because cases move unidirectionally forward through the legal lifecycle:
    C(k, t) = sum of cases at stage k or beyond (including completed cases).
Then for any two dates t1 -> t2:
    Delta C(k) = C(k, t2) - C(k, t1)
is the EXACT number of cases that crossed Gate k-1 in that window.

Because new intake arrives strictly at Stage 1 (No Action Taken),
for all gates k >= 2, intake cancels out completely. This yields
100% INTAKE-PROOF THROUGHPUT without requiring individual case IDs.

The 4 Standard Gates:
---------------------
G1 (Reducing No Action)     = Delta C(LCO Stage onward)
G2 (Sending LCO Proposal)   = Delta C(GA/LCO Authorization onward)
G3 (Bringing to HC Hearing) = Delta C(Affidavit Filed - Hearing Stage onward)
G4 (Reporting Compliance)   = Delta C(Proposed for Appeal + Closed with Appeal No + Not to Appeal)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Stage order from intake to final completion
STAGE_ORDER_WP = [
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


def extract_stage_stocks(metrics: dict[str, Any]) -> dict[str, int]:
    """Extract standard integer stocks for each stage from metrics dict."""
    stocks = {}
    for st in STAGE_ORDER_WP:
        v = metrics.get(st, 0)
        try:
            stocks[st] = int(v) if v is not None else 0
        except (ValueError, TypeError):
            stocks[st] = 0
    return stocks


def compute_cumulative_reverse_stocks(metrics: dict[str, Any]) -> dict[str, int]:
    """
    Compute C(k) = sum of cases at stage k or beyond.
    Returns:
        C_G1: Stock from LCO proposal onward (all non-No-Action cases)
        C_G2: Stock from GA/LCO Auth onward (all counsel authorized or pre-trial cases)
        C_G3: Stock from HC Hearing onward (all hearing, interim, and compliance cases)
        C_G4: Stock of completed cases (Proposed Appeal, Closed with Appeal, Not to Appeal)
    """
    m = metrics or {}
    
    # Completed bucket
    completed = (
        int(m.get("proposed_for_appeal") or 0) +
        int(m.get("closed_with_appeal_number") or 0) +
        int(m.get("not_to_appeal") or 0)
    )
    
    # Compliance pending + completed
    c_post_trial = (
        int(m.get("final_order_compliance_pending") or 0) +
        int(m.get("interim_order_compliance_pending") or 0) +
        int(m.get("disposed_case") or 0) +
        completed
    )
    
    # Hearing stage onward
    c_g3 = int(m.get("affidavit_filed_hearing_stage") or 0) + c_post_trial
    
    # Pre-trial draft/advocate pending
    c_pre_trial = (
        int(m.get("draft_pwr_pending") or 0) +
        int(m.get("approved_pwr_pending") or 0) +
        int(m.get("draft_so_pending_from_advocate") or 0) +
        int(m.get("approved_so_pending") or 0) +
        int(m.get("affidavit_filing_pending") or 0) +
        c_g3
    )
    
    # GA / LCO Auth onward
    c_g2 = int(m.get("ga_lco_authorization_pending") or 0) + c_pre_trial
    
    # LCO Proposal onward (everything except No Action)
    c_g1 = int(m.get("lco_proposal_stage_pending") or 0) + c_g2
    
    return {
        "C_G1": c_g1,
        "C_G2": c_g2,
        "C_G3": c_g3,
        "C_G4": completed,
    }


def compute_gate_throughputs(
    curr_metrics: dict[str, Any],
    base_metrics: dict[str, Any],
    intake_inflow: int = 0
) -> dict[str, Any]:
    """
    Compute physically accurate stage clearances across all 4 gates:
    G1: Intake Cleared = max(0, base_no_act - curr_no_act)
    G2: LCO Processed / GA Authorized = max(0, base_lco - curr_lco) + max(0, curr_ga - base_ga)
    G3: Draft Remarks Cleared & Brought to Court = max(0, base_pwr - curr_pwr) + max(0, curr_hearing - base_hearing)
    G4: Final Orders Complied & Disposed = max(0, base_orders - curr_orders)
    """
    curr = curr_metrics or {}
    base = base_metrics or {}
    
    curr_pending = int(curr.get("total_cases_pending") or 0)
    base_pending = int(base.get("total_cases_pending") or curr_pending or 0)
    
    curr_no_act = int(curr.get("no_action_taken") or 0)
    base_no_act = int(base.get("no_action_taken") or 0)
    
    curr_lco = int(curr.get("lco_proposal_stage_pending") or 0)
    base_lco = int(base.get("lco_proposal_stage_pending") or 0)
    
    curr_ga = int(curr.get("ga_lco_authorization_pending") or 0)
    base_ga = int(base.get("ga_lco_authorization_pending") or 0)
    
    curr_pwr = int(curr.get("draft_pwr_pending") or 0)
    base_pwr = int(base.get("draft_pwr_pending") or 0)
    
    curr_hearing = int(curr.get("affidavit_filed_hearing_stage") or 0)
    base_hearing = int(base.get("affidavit_filed_hearing_stage") or 0)
    
    curr_orders = int(curr.get("final_order_compliance_pending") or 0)
    base_orders = int(base.get("final_order_compliance_pending") or 0)
    
    # Real stage movements
    g1 = max(0, base_no_act - curr_no_act)
    g2 = max(0, base_lco - curr_lco) + max(0, curr_ga - base_ga)
    g3 = max(0, base_pwr - curr_pwr) + max(0, curr_hearing - base_hearing)
    g4 = max(0, base_orders - curr_orders)
    
    # Also credit overall caseload drop if final order was closed/disposed
    net_drop = max(0, base_pending - curr_pending)
    if net_drop > g4:
        g4 = net_drop

    total_crossings = g1 + g2 + g3 + g4
    
    # Normalized conversion rates against Stage-Specific Opening Balance
    rate_g1 = min(1.0, g1 / max(1, base_no_act)) if base_no_act > 0 else (1.0 if g1 > 0 else 0.0)
    rate_g2 = min(1.0, g2 / max(1, base_lco)) if base_lco > 0 else (1.0 if g2 > 0 else 0.0)
    rate_g3 = min(1.0, g3 / max(1, base_pwr)) if base_pwr > 0 else (1.0 if g3 > 0 else 0.0)
    rate_g4 = min(1.0, g4 / max(1, base_orders)) if base_orders > 0 else (1.0 if g4 > 0 else 0.0)
    
    composite_score = (
        (0.10 * rate_g1) +
        (0.15 * rate_g2) +
        (0.45 * rate_g3) +
        (0.30 * rate_g4)
    ) * 100.0
    
    no_action_share = (curr_no_act / max(1, curr_pending)) * 100.0 if curr_pending > 0 else 0.0
    
    return {
        "opening_balance": base_pending,
        "base_no_act": base_no_act,
        "base_lco": base_lco,
        "base_pre_trial": base_pwr,
        "base_final_orders": base_orders,
        "G1_intake_reduced": g1,
        "G2_lco_dispatched": g2,
        "G3_hearing_reached": g3,
        "G4_compliance_closed": g4,
        "total_gate_crossings": total_crossings,
        "rate_g1_pct": round(rate_g1 * 100.0, 1),
        "rate_g2_pct": round(rate_g2 * 100.0, 1),
        "rate_g3_pct": round(rate_g3 * 100.0, 1),
        "rate_g4_pct": round(rate_g4 * 100.0, 1),
        "composite_score": round(composite_score, 1),
        "net_backlog_minus_intake": (curr_pending - base_pending) - intake_inflow,
        "no_action_share_pct": round(no_action_share, 1),
    }


def generate_stagnation_report(
    divisions_with_metrics: list[dict[str, Any]],
    threshold_pending: int = 15
) -> list[dict[str, Any]]:
    """
    Daily Exception Report: Identifies all divisions/officers with >= threshold_pending
    cases that had ZERO gate crossings (total_gate_crossings == 0).
    """
    stagnant_units = []
    for d in divisions_with_metrics:
        pending = d.get("total_pending", 0)
        crossings = d.get("scorecard", {}).get("total_gate_crossings", 0)
        
        if pending >= threshold_pending and crossings == 0:
            stagnant_units.append({
                "code": d.get("code"),
                "name": d.get("name"),
                "circle": d.get("circle"),
                "total_pending": pending,
                "no_action": d.get("metrics", {}).get("no_action_taken", 0),
                "draft_pwr": d.get("metrics", {}).get("draft_pwr_pending", 0),
                "final_orders": d.get("metrics", {}).get("final_order_compliance_pending", 0),
                "no_action_share_pct": d.get("scorecard", {}).get("no_action_share_pct", 0),
            })
            
    stagnant_units.sort(key=lambda x: x["total_pending"], reverse=True)
    return stagnant_units


def compute_littles_law_dwell_times(
    current_metrics: dict[str, Any],
    avg_daily_throughput: dict[str, float]
) -> dict[str, float]:
    """
    Little's Law: Average Dwell Time (Days) ≈ Stage Stock / Average Daily Throughput Past Gate
    W = L / lambda
    """
    m = current_metrics or {}
    dwell = {}
    
    no_act_stock = int(m.get("no_action_taken") or 0)
    g1_lambda = avg_daily_throughput.get("G1", 0.0)
    dwell["no_action_dwell_days"] = round(no_act_stock / g1_lambda, 1) if g1_lambda > 0 else None
    
    draft_pwr_stock = int(m.get("draft_pwr_pending") or 0)
    g3_lambda = avg_daily_throughput.get("G3", 0.0)
    dwell["draft_pwr_dwell_days"] = round(draft_pwr_stock / g3_lambda, 1) if g3_lambda > 0 else None
    
    final_orders_stock = int(m.get("final_order_compliance_pending") or 0)
    g4_lambda = avg_daily_throughput.get("G4", 0.0)
    dwell["final_orders_dwell_days"] = round(final_orders_stock / g4_lambda, 1) if g4_lambda > 0 else None
    
    return dwell
