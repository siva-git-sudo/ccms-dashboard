"""
Parser for CCMS (Karnataka Forest Department Court Case Management System)
"OutsideSecretariat" report XML export.

The report is an SSRS (Microsoft Reporting Services) report rendered through
an ASP.NET ReportViewer control. When exported as "XML file with report
data" it produces a small, well-structured XML document shaped like:

<Report ...>
  <table1>
    <Textbox1/><Textbox351/>
    <MajDeptNm_Collection>
      <MajDeptNm MajDeptNm="Forest, Ecology  and Environment" Textbox97="0" Textbox107="18" ...>
        <table1_MinDeptNm_Collection>
          <table1_MinDeptNm MinDeptNm="Bengaluru Circle" Textbox77="0" Textbox92="18" ...>
            <table1_SecNm_Collection> ... </table1_SecNm_Collection>
          </table1_MinDeptNm>
        </table1_MinDeptNm_Collection>
      </MajDeptNm>
    </MajDeptNm_Collection>
    <Textbox91 Textbox90="0" Textbox95="18" .../>   <-- grand total footer row
  </table1>
</Report>

Every level (MajDeptNm, MinDeptNm, SecNm, PostNm, and the Textbox91 footer)
carries the SAME 17 numeric columns in the SAME left-to-right order as the
report's visible column headers. The Textbox* id numbers differ per level
(SSRS assigns a fresh id to every rendered cell), so columns must be read
positionally rather than by name.

Column order (confirmed against the "OutsideSecretariat.xlsx" sample export
for Bengaluru Circle -- header row + numeric column-id row):

  1. cases_received_as_on_yesterday
  2. total_cases_pending            <- headline "pending cases" figure
  3. no_action_taken
  4. lco_proposal_stage_pending
  5. ga_lco_authorization_pending
  6. draft_pwr_pending
  7. approved_pwr_pending
  8. draft_so_pending_from_advocate
  9. approved_so_pending
 10. affidavit_filing_pending
 11. affidavit_filed_hearing_stage
 12. interim_order_compliance_pending
 13. disposed_case
 14. final_order_compliance_pending
 15. proposed_for_appeal
 16. closed_with_appeal_number
 17. not_to_appeal

We read the report's grand-total footer row (the lone element directly
under <table1> that is not a "*_Collection") because it always reflects the
totals for whichever single division/circle was selected in the ddldeptname
filter -- regardless of how many Section/Designation rows the report
expands into underneath it. That keeps parsing robust even if a division
has a deep or shallow office hierarchy.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

LAYOUT_17 = [
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

LAYOUT_20_CCC = [
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
    "affidavit_filed_hearing_stage",
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

LAYOUT_16_WA = [
    "cases_received_as_on_yesterday",
    "total_cases_pending",
    "no_action_taken",
    "ga_lco_authorization_pending",
    "ga_lco_authorization_for_appeal",
    "draft_memo_of_appeal_with_ga_or_dept",
    "interim_final_order_of_hc",
    "filing_of_interim_application",
    "writ_appeal_affidavit_filed",
    "affidavit_filed_hearing_stage",
    "interim_order_compliance_pending",
    "disposed_case",
    "final_order_compliance_pending",
    "compliance_affidavit_filing_for_interim_order_pending",
    "compliance_affidavit_of_final_order_filing_pending",
    "not_to_appeal",
]

COLUMN_LABELS = LAYOUT_17

# Human-readable headers across all report layouts
COLUMN_HEADERS = {
    # Core & Layout 17
    "cases_received_as_on_yesterday": "Cases Received As On Yesterday",
    "total_cases_pending": "Total Cases Pending",
    "no_action_taken": "No Action Taken",
    "lco_proposal_stage_pending": "LCO Proposal Stage (Pending from Department)",
    "ga_lco_authorization_pending": "GA/LCO Authorization Pending",
    "draft_pwr_pending": "Draft PWR Pending",
    "approved_pwr_pending": "Approved PWR Pending",
    "draft_so_pending_from_advocate": "Draft SO Pending from Advocate",
    "approved_so_pending": "Approved SO Pending",
    "affidavit_filing_pending": "Affidavit Filing Pending",
    "affidavit_filed_hearing_stage": "Affidavit Filed Hearing Stage",
    "interim_order_compliance_pending": "Interim Order Compliance Pending",
    "disposed_case": "Disposed Case",
    "final_order_compliance_pending": "Final Order Compliance Pending",
    "proposed_for_appeal": "Proposed for Appeal",
    "closed_with_appeal_number": "Closed with Appeal Number",
    "not_to_appeal": "Not to Appeal",

    # Layout 20 (Civil Contempt Petition)
    "pending_with_lco_accused": "Pending with LCO / Accused",
    "draft_affidavit_by_lco_accused": "Draft Affidavit by LCO / Accused",
    "draft_compliance_affidavit_by_advocate": "Draft Compliance Affidavit by Advocate",
    "final_compliance_affidavit_done_filing_pending": "Final Compliance Affidavit done, Filing pending",
    "compliance_affidavit_not_filed": "Compliance Affidavit Not Filed",
    "appeal_filed_not_admitted": "Appeal Filed not Admitted",
    "appeal_filed_and_admitted": "Appeal Filed and Admitted",
    "appeal_filed_and_stay_granted": "Appeal Filed and Stay Granted",

    # Layout 16 (Writ Appeal / S-KSAT)
    "ga_lco_authorization_for_appeal": "GA LCO Authorization For Appeal",
    "draft_memo_of_appeal_with_ga_or_dept": "Draft Memo of Appeal with GA / Dept",
    "interim_final_order_of_hc": "Interim / Final Order of HC",
    "filing_of_interim_application": "Filing of Interim Application",
    "writ_appeal_affidavit_filed": "Writ Appeal Affidavit Filed",
    "compliance_affidavit_filing_for_interim_order_pending": "Compliance Affidavit Filing For Interim Order Pending",
    "compliance_affidavit_of_final_order_filing_pending": "Compliance Affidavit of Final Order Filing Pending",
}

# Columns sitting under a "Completed Case" group header
COMPLETED_CASE_GROUP = [
    "proposed_for_appeal",
    "closed_with_appeal_number",
    "not_to_appeal",
]


def labels_for_column_count(n_columns: int) -> list[str]:
    """Map column count to known layout labels."""
    if n_columns == 20:
        return LAYOUT_20_CCC
    if n_columns == 16:
        return LAYOUT_16_WA
    if n_columns == 17:
        return LAYOUT_17
    
    labels = ["cases_received_as_on_yesterday", "total_cases_pending"]
    for i in range(2, n_columns):
        labels.append(f"col_{i + 1}")
    return labels


def columns_for_layout(n_columns: int) -> list[dict]:
    """Return [{key, header, known}] describing a report's columns."""
    labels = labels_for_column_count(n_columns)
    return [
        {
            "key": k,
            "header": COLUMN_HEADERS.get(k, k.replace("_", " ").title()),
            "known": not k.startswith("col_")
        }
        for k in labels
    ]


def _strip_namespaces(elem: ET.Element) -> None:
    """Remove '{namespace}' prefixes from every tag so we can match plain
    local names regardless of the report's xmlns (which is derived from the
    report name, e.g. xmlns="OutsideSecretariat")."""
    for e in elem.iter():
        if "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]


def _to_number(value: str):
    try:
        if "." in value:
            return float(value)
        return int(value)
    except (TypeError, ValueError):
        return value


def parse_ccms_xml(path: str | Path) -> dict:
    """Parse one CCMS 'OutsideSecretariat' XML export and return the
    division/circle's headline totals plus the raw footer attributes."""
    tree = ET.parse(path)
    root = tree.getroot()
    _strip_namespaces(root)

    table1 = root.find(".//table1")
    if table1 is None:
        raise ValueError(f"{path}: no <table1> element found -- not a CCMS report export?")

    # The footer/grand-total row is the LAST direct child of <table1> that
    # (a) is not a "*_Collection" wrapper and (b) actually carries the
    # numeric column attributes. table1 also contains empty leading
    # placeholder textboxes (e.g. <Textbox1/>, <Textbox351/>) with no
    # attributes at all -- skip those.
    footer = None
    for child in table1:
        if child.tag.endswith("_Collection"):
            continue
        if len(child.attrib) == 0:
            continue
        footer = child  # keep overwriting -> ends up as the last match

    if footer is None:
        raise ValueError(f"{path}: could not find the report's grand-total footer row")

    raw_attrs = list(footer.attrib.items())
    values = [_to_number(v) for _, v in raw_attrs]

    result: dict = {"raw": dict(raw_attrs), "footer_tag": footer.tag}

    if len(values) < 2:
        result["warning"] = (
            f"footer row has only {len(values)} column(s); cannot read totals, check 'raw'"
        )
        result["values_unmapped"] = values
        result["total_cases_pending"] = None
        return result

    # The footer row has no leading name attribute -- it is all figures --
    # so the two core columns are at positions 0 and 1 here.
    labels = labels_for_column_count(len(values))
    for label, value in zip(labels, values):
        result[label] = value
    result["_columns"] = values
    result["_column_count"] = len(values)
    return result


def _row_from_attrs(attrs: dict) -> dict | None:
    """attrs is an element's .attrib: the name attribute first, then the
    report's numeric columns in left-to-right order.
    """
    items = list(attrs.items())
    if len(items) < 3:
        return None  # need a name + at least the two core figures

    values = [_to_number(v) for _, v in items[1:]]  # skip the name attr

    labels = labels_for_column_count(len(values))
    row = dict(zip(labels, values))
    row["_columns"] = values
    row["_column_count"] = len(values)
    return row


def parse_ccms_xml_officers(path: str | Path) -> list[dict]:
    """Parse one CCMS XML export down to the 'user'/officer level -- the
    <table1_PostNm> rows, which are the individual office/designation
    entries within a division's report (e.g. 'CF BENGALURU', 'Conservator
    of Forests', 'Assistant Administrator'). Each PostNm sits under a
    SecNm (section/office) under the single division selected via
    ddldeptname.

    Returns a list of:
        {"section": <SecNm>, "post": <PostNm>, <17 numeric fields>}
    """
    tree = ET.parse(path)
    root = tree.getroot()
    _strip_namespaces(root)

    table1 = root.find(".//table1")
    if table1 is None:
        raise ValueError(f"{path}: no <table1> element found -- not a CCMS report export?")

    parent_of = {child: parent for parent in table1.iter() for child in parent}

    rows = []
    for post_elem in table1.iter("table1_PostNm"):
        row = _row_from_attrs(post_elem.attrib)
        if row is None:
            continue  # unexpected column count -- skip rather than mis-map
        post_name = next(iter(post_elem.attrib.values()), "").strip()

        sec_elem = parent_of.get(parent_of.get(post_elem))  # PostNm -> *_Collection -> SecNm
        section_name = ""
        if sec_elem is not None and sec_elem.tag == "table1_SecNm":
            section_name = sec_elem.attrib.get("SecNm", "").strip()

        rows.append({"section": section_name, "post": post_name, **row})

    return rows


def normalize_name(name: str) -> str:
    """Collapse whitespace and lowercase, for matching division names
    between the CCMS dropdown option text and the report's rendered
    MinDeptNm values (which sometimes carry extra internal spacing, e.g.
    'Forest, Ecology  and Environment' with a double space)."""
    return " ".join((name or "").split()).lower()


def parse_ccms_xml_by_division(path: str | Path) -> dict[str, dict]:
    """Parse a CCMS export made with ddldeptname='0' (--All--), which
    returns every circle/division/SF-division under the selected
    secretariat dept in ONE report instead of one report per division.

    Returns {division_name: {"totals": {...17 fields}, "officers": [...]}}
    for every <table1_MinDeptNm> found -- i.e. every circle/division in
    the report, not just the ones we care about. Callers should filter
    down to their target list by name (see normalize_name for matching).
    """
    tree = ET.parse(path)
    root = tree.getroot()
    _strip_namespaces(root)

    table1 = root.find(".//table1")
    if table1 is None:
        raise ValueError(f"{path}: no <table1> element found -- not a CCMS report export?")

    parent_of = {child: parent for parent in table1.iter() for child in parent}

    result: dict[str, dict] = {}
    for min_elem in table1.iter("table1_MinDeptNm"):
        div_name = min_elem.attrib.get("MinDeptNm", "").strip()
        totals = _row_from_attrs(min_elem.attrib)
        if not div_name or totals is None:
            continue

        officers = []
        for post_elem in min_elem.iter("table1_PostNm"):
            row = _row_from_attrs(post_elem.attrib)
            if row is None:
                continue
            post_name = next(iter(post_elem.attrib.values()), "").strip()
            sec_elem = parent_of.get(parent_of.get(post_elem))
            section_name = (
                sec_elem.attrib.get("SecNm", "").strip()
                if sec_elem is not None and sec_elem.tag == "table1_SecNm"
                else ""
            )
            officers.append({"section": section_name, "post": post_name, **row})

        result[div_name] = {"totals": totals, "officers": officers}

    return result


def sum_officer_rows(officer_row_lists: list[list[dict]]) -> list[dict]:
    """Merge officer/post rows from several report pulls (e.g. HC WP + HC
    WA + KSAT OA ...) into one list, summing numeric fields for rows that
    share the same (section, post)."""
    merged: dict[tuple[str, str], dict] = {}
    for rows in officer_row_lists:
        for row in rows:
            key = (row["section"], row["post"])
            if key not in merged:
                merged[key] = {
                    "section": row["section"],
                    "post": row["post"],
                    **{label: 0 for label in COLUMN_LABELS},
                }
            for label in COLUMN_LABELS:
                v = row.get(label)
                if isinstance(v, (int, float)):
                    merged[key][label] += v
    return list(merged.values())


def parse_many(paths: dict[str, str | Path]) -> dict:
    """paths: {division_code: xml_file_path} -> {division_code: parsed dict}"""
    out = {}
    for code, path in paths.items():
        out[code] = parse_ccms_xml(path)
    return out


def sum_parsed(parsed_dicts: list[dict]) -> dict:
    """Sum several parse_ccms_xml() results column-wise (e.g. across
    High Court + KSAT, and across every case type within each court), to
    get one combined 'all courts, all case types' total per division.
    Dicts with a 'warning' (unmapped columns) are skipped for the numeric
    sum but noted.

    IMPORTANT: if there is nothing valid to sum (empty input, or every
    input skipped), the numeric fields come back as None -- NOT 0. A
    failed scrape must not be indistinguishable from a division that
    genuinely has zero pending cases."""
    # Sum every label we know about, plus whatever keys the rows actually
    # carry -- different case-type reports expose different column sets,
    # so we cannot assume the 17 Writ Petition labels are present.
    all_labels = set(COLUMN_LABELS) | {"cases_received_as_on_yesterday", "total_cases_pending"}
    for d in parsed_dicts:
        if isinstance(d, dict):
            all_labels.update(
                k for k, v in d.items()
                if isinstance(v, (int, float)) and not k.startswith("_")
            )

    totals = {label: 0 for label in all_labels}
    present = {label: False for label in all_labels}
    skipped = 0
    counted = 0
    for d in parsed_dicts:
        if d is None or "warning" in d:
            skipped += 1
            continue
        counted += 1
        for label in all_labels:
            v = d.get(label)
            if isinstance(v, (int, float)):
                totals[label] += v
                present[label] = True

    if counted == 0:
        result = {label: None for label in all_labels}
    else:
        # A label no row actually reported stays None rather than a
        # misleading 0.
        result = {label: (totals[label] if present[label] else None) for label in all_labels}

    result["_combined_from"] = counted
    if skipped:
        result["_skipped"] = skipped
    return result


if __name__ == "__main__":
    import json
    import sys

    for p in sys.argv[1:]:
        print(f"=== {p} ===")
        print(json.dumps(parse_ccms_xml(p), indent=2))
