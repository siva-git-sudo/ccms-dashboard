"""Canonical stage crosswalk for the eight CCMS "Outside Secretariat" reports.

WHY THIS EXISTS
---------------
parse_ccms_xml.py applies its stage labels only when a report has exactly
17 numeric columns. That is true for Writ Petition and for all four KSAT
reports, but NOT for three of them -- and the reason is not cosmetic:

    layout   columns  reports                    ladder
    A        17       WP, OA, CA, MA, RA         standard writ ladder
    B        20       CCC (Civil Contempt)       contempt ladder
    C        16       WA, S-KSAT                 appeal ladder

These are genuinely different workflows, not the same workflow with extra
columns. A contempt case never has a "Draft PWR"; an appeal never has an
"LCO Proposal Stage". So the old behaviour (dump everything into col_3 ..
col_20) lost 210 cases' worth of stage detail, and summing the layouts
together was adding unlike things.

Column orders below were read off data/raw/<date>/*.xml attribute order
and verified arithmetically: for every layout, the stage columns sum
exactly to Total Cases Pending. See verify_stage_map() at the bottom.

Group headers span multiple data columns and are expanded here:
  layout A  "14 Completed Case"  -> proposed_for_appeal, closed_with_appeal_number, not_to_appeal
  layout B  "11. Appealed"       -> appeal_filed_not_admitted, _and_admitted, _and_stay_granted
  layout B  "15. Completed Case" -> proposed_for_appeal, closed_with_appeal_number, not_to_appeal
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Positional column order, per layout. Index 0 and 1 are stable across all
# three: cases received yesterday, then total pending.
# --------------------------------------------------------------------------

LAYOUT_A = [
    "cases_received_as_on_yesterday",
    "total_cases_pending",
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

LAYOUT_B = [
    "cases_received_as_on_yesterday",
    "total_cases_pending",
    "no_action_taken",
    "lco_proposal_stage_pending",
    "ga_lco_authorization_pending",
    "pending_with_lco_accused",
    "draft_affidavit_by_lco_accused",
    "draft_compliance_affidavit_by_advocate",
    "final_compliance_affidavit_done_filing_pending",
    "compliance_affidavit_not_filed",
    "compliance_affidavit_filed_hearing_stage",
    "appeal_filed_not_admitted",
    "appeal_filed_and_admitted",
    "appeal_filed_and_stay_granted",
    "interim_order_compliance_pending",
    "disposed_case",
    "final_order_compliance_pending",
    "proposed_for_appeal",
    "closed_with_appeal_number",
    "not_to_appeal",
]

LAYOUT_C = [
    "cases_received_as_on_yesterday",
    "total_cases_pending",
    "no_action_taken",
    "ga_lco_auth_pending",
    "ga_lco_authorization_for_appeal",
    "draft_memo_of_appeal",
    "interim_final_order_of_hc",
    "filing_of_interim_application",
    "writ_appeal_affidavit_filed",
    "hearing",
    "interim_order_compliance_pending",
    "disposed_case",
    "final_order_compliance_pending",
    "compliance_affidavit_filing_for_interim_order_pending",
    "compliance_affidavit_of_final_order_filing_pending",
    "completed_case",
]

LAYOUT_BY_COMBO = {
    "court1_WP": LAYOUT_A,
    "court3_OA": LAYOUT_A,
    "court3_CA": LAYOUT_A,
    "court3_MA": LAYOUT_A,
    "court3_RA": LAYOUT_A,
    "court1_CCC": LAYOUT_B,
    "court1_WA": LAYOUT_C,
    "court1_S-KSAT": LAYOUT_C,
}

# Resolve by column count as a fallback when the combo name is unknown.
LAYOUT_BY_WIDTH = {17: LAYOUT_A, 20: LAYOUT_B, 16: LAYOUT_C}


# --------------------------------------------------------------------------
# Canonical five-stage spine.
#
# The three ladders are not comparable column-by-column, but they ARE
# comparable at this level of abstraction -- which is exactly the level the
# performance question is asked at: has the case been picked up, has the
# department's own paperwork gone out, has it reached a hearing, has
# compliance been reported.
#
# Order matters. A case only moves forward, so "at stage k or beyond" is
# a monotone quantity and its increase measures throughput past gate k-1.
# --------------------------------------------------------------------------

SPINE = ["no_action", "lco_proposal", "preparation", "hearing", "compliance", "completed"]

SPINE_LABELS = {
    "no_action": "No action taken",
    "lco_proposal": "LCO proposal pending with department",
    "preparation": "Departmental preparation (PWR / SO / affidavit)",
    "hearing": "Reached hearing stage",
    "compliance": "Order compliance pending",
    "completed": "Completed",
}

# NOTE ON THE APPEAL LADDER
# Layouts C (Writ Appeal, S-KSAT) have no LCO proposal step at all -- an appeal
# runs off the existing authorisation. Their `lco_proposal` stage is therefore
# always 0, which means C(lco_proposal) == C(preparation) and the "left No
# Action" and "LCO done" counts coincide for those case types. That is correct:
# for an appeal, leaving No Action IS clearing the LCO gate, because there is
# no LCO gate to clear.

CROSSWALK = {
    "A": {
        "no_action": ["no_action_taken"],
        "lco_proposal": ["lco_proposal_stage_pending"],
        "preparation": [
            "ga_lco_authorization_pending",
            "draft_pwr_pending",
            "approved_pwr_pending",
            "draft_so_pending_from_advocate",
            "approved_so_pending",
            "affidavit_filing_pending",
        ],
        "hearing": ["affidavit_filed_hearing_stage"],
        "compliance": [
            "interim_order_compliance_pending",
            "disposed_case",
            "final_order_compliance_pending",
        ],
        "completed": [
            "proposed_for_appeal",
            "closed_with_appeal_number",
            "not_to_appeal",
        ],
    },
    "B": {
        "no_action": ["no_action_taken"],
        "lco_proposal": ["lco_proposal_stage_pending"],
        "preparation": [
            "ga_lco_authorization_pending",
            "pending_with_lco_accused",
            "draft_affidavit_by_lco_accused",
            "draft_compliance_affidavit_by_advocate",
            "final_compliance_affidavit_done_filing_pending",
            "compliance_affidavit_not_filed",
        ],
        "hearing": [
            "compliance_affidavit_filed_hearing_stage",
            "appeal_filed_not_admitted",
            "appeal_filed_and_admitted",
            "appeal_filed_and_stay_granted",
        ],
        "compliance": [
            "interim_order_compliance_pending",
            "disposed_case",
            "final_order_compliance_pending",
        ],
        "completed": [
            "proposed_for_appeal",
            "closed_with_appeal_number",
            "not_to_appeal",
        ],
    },
    "C": {
        "no_action": ["no_action_taken"],
        "lco_proposal": [],          # appeals have no LCO proposal step
        "preparation": [
            "ga_lco_auth_pending",
            "ga_lco_authorization_for_appeal",
            "draft_memo_of_appeal",
            "interim_final_order_of_hc",
            "filing_of_interim_application",
            "writ_appeal_affidavit_filed",
        ],
        "hearing": ["hearing"],
        "compliance": [
            "interim_order_compliance_pending",
            "disposed_case",
            "final_order_compliance_pending",
            "compliance_affidavit_filing_for_interim_order_pending",
            "compliance_affidavit_of_final_order_filing_pending",
        ],
        "completed": ["completed_case"],
    },
}

LAYOUT_NAME = {id(LAYOUT_A): "A", id(LAYOUT_B): "B", id(LAYOUT_C): "C"}

# The two stages the department is judged on directly: cases standing at
# hearing, and final orders still awaiting compliance. These are narrower than
# the `hearing` and `compliance` spine stages -- `compliance` also carries
# interim-order and disposed cases, which are not what "final order pending"
# means to a reviewer.
FINAL_ORDER = {
    "A": ["final_order_compliance_pending"],
    "B": ["final_order_compliance_pending"],
    "C": ["final_order_compliance_pending",
          "compliance_affidavit_of_final_order_filing_pending"],
}

# The "LCO proposal sent" gate is a sub-gate of `preparation`. It exists only
# in the writ and contempt ladders -- an appeal has no fresh LCO proposal
# step -- so it is scored on layouts A and B only, and layout C rows are
# excluded from the denominator rather than counted as failures.
LCO_PENDING_FIELD = "lco_proposal_stage_pending"
LCO_APPLICABLE_LAYOUTS = {"A", "B"}


def layout_for(combo: str, width: int | None = None):
    """Return (layout_columns, layout_name) for a report combo key."""
    cols = LAYOUT_BY_COMBO.get(combo)
    if cols is None and width is not None:
        cols = LAYOUT_BY_WIDTH.get(width)
    if cols is None:
        return None, None
    return cols, LAYOUT_NAME[id(cols)]


def map_row(values: list, combo: str) -> dict | None:
    """Map a raw positional value list onto named columns for its layout."""
    cols, name = layout_for(combo, len(values))
    if cols is None or len(values) != len(cols):
        return None
    row = dict(zip(cols, values))
    row["_layout"] = name
    return row


def to_spine(row: dict) -> dict:
    """Collapse a mapped row onto the canonical five-stage spine."""
    name = row.get("_layout")
    walk = CROSSWALK.get(name)
    if walk is None:
        return {}
    out = {}
    for stage in SPINE:
        out[stage] = sum(row.get(f) or 0 for f in walk[stage])
    out["intake"] = row.get("cases_received_as_on_yesterday") or 0
    # Pending is taken as the sum of the stage columns, NOT the report's own
    # "Total Cases Pending" figure. On a handful of rows CCMS declares a total
    # of 0 while still showing stage detail; the department-level grand total
    # counts those cases, so the declared leaf total is the wrong number. Using
    # the stage sum makes the leaf rows reconcile to CCMS's own grand total.
    out["pending"] = sum(out[s] for s in SPINE if s != "completed")
    out["pending_declared"] = row.get("total_cases_pending") or 0
    out["final_order"] = sum(row.get(f) or 0 for f in FINAL_ORDER[name])
    out["_layout"] = name
    if name in LCO_APPLICABLE_LAYOUTS:
        out["lco_pending"] = row.get(LCO_PENDING_FIELD) or 0
    return out


def cumulative(spine_row: dict) -> dict:
    """C[k] = cases at spine stage k or beyond.

    New cases enter only at `no_action`, so for every k >= 1 the change in
    C[k] between two dates equals the number of cases that crossed gate
    k-1 in that window -- intake cancels out entirely. This is the whole
    basis of the throughput measure.
    """
    out, run = {}, 0
    for stage in reversed(SPINE):
        run += spine_row.get(stage, 0) or 0
        out[stage] = run
    return out


def verify_stage_map(row: dict) -> tuple[bool, int, int]:
    """Stage columns must sum exactly to Total Cases Pending.

    This is the arithmetic identity that proves a layout's column order is
    right. Run it over every parsed row; any failure means the report
    layout changed upstream and the map needs revisiting.
    """
    name = row.get("_layout")
    walk = CROSSWALK[name]
    pending_stages = [s for s in SPINE if s != "completed"]
    total = sum(
        row.get(f) or 0 for stage in pending_stages for f in walk[stage]
    )
    declared = row.get("total_cases_pending") or 0
    return total == declared, total, declared
