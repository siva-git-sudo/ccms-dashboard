#!/usr/bin/env python3
"""
Scrapes the Karnataka Forest Department CCMS "Non Secretariat Department"
report (StateReport.aspx), across BOTH courts (High Court of Karnataka and
KSAT) and EVERY case type each court offers, and filters the result down
to a fixed list of circles/divisions -- summing each division's numbers
into one "all courts, all case types" total, snapshotting today's numbers,
and regenerating public/data.json for the dashboard.

WHY A BROWSER (PLAYWRIGHT) AND NOT PLAIN HTTP REQUESTS
-------------------------------------------------------
StateReport.aspx is a classic ASP.NET WebForms page built around a
Microsoft ReportViewer control (Microsoft.Reporting.WebForms). Every
interaction -- changing a dropdown, viewing the report, exporting -- is a
postback that requires a live __VIEWSTATE / __EVENTVALIDATION pair issued
by the server for that exact session, plus the ReportViewer's own internal
report-execution session. These are opaque, single-use, and expire
quickly. Reverse-engineering that handshake outside a real browser session
is brittle; driving the actual page with Playwright is what a human doing
this by hand would do, and is far more robust to the server's internals
changing.

RUN
---
    pip install -r requirements.txt
    playwright install chromium
    python scrape_ccms.py

Run this daily (cron / Task Scheduler / GitHub Actions) from a machine
that has network access to ccms.karnataka.gov.in. It needs no login (the
report is public).

ONE "--All--" PULL PER COMBO, THEN FILTER
-------------------------------------------
The department dropdown (ddldeptname) has an "--All--" option (value
"0"). Selecting it returns every circle/division/SF-division under the
Forest, Ecology & Environment department in ONE report, instead of one
report per division. So instead of looping (12 divisions x 7 court/case
type combos = 84 page loads), this script loops just the 7 combos below,
pulls the "--All--" report each time, and filters the parsed result down
to the 12 target divisions in divisions.json (matched by name -- see
normalize_name in parse_ccms_xml.py). Much faster and less brittle.

IF SELECTORS BREAK
-------------------
Built from a HAR capture, manual exports, and one real failed run against
the live site (which is how the rblstatereport-must-come-first ordering
below was found -- ddlsecdeptname/ddldeptname are hidden until the
"Non Secretariat Department" radio is picked). The "View Report" button
and the "Export > XML" menu path are still NOT confirmed against the live
site -- ReportViewer's default control labels are used as the first guess
(see VIEW_REPORT_CANDIDATES / EXPORT_XML_CANDIDATES below). If a run
fails at those steps, run with `HEADLESS=false python scrape_ccms.py`,
watch where it gets stuck, and paste the exact button/menu text back so
the candidate list can be corrected.

COURTS AND CASE TYPES
----------------------
The case-type dropdown (ddlCasetype) repopulates depending on which court
(ddlcourtname) is selected -- it's a dependent dropdown, not a fixed list.
This script pulls one "--All--" report per (court, case type) combination
below and SUMS matching divisions' results into one "all cases" total, so
the dashboard shows one number per division covering both courts and
every case type:

  High Court of Karnataka (ddlcourtname=1), bench=-All-:
    CCC   Civil Contempt Petition
    WA    Writ Appeal
    WP    Writ Petition

  Karnataka State Administrative Tribunal / KSAT (ddlcourtname=3):
    CA    Contempt Application
    MA    Miscellaneous Application
    OA    Original Application   (note: site's option value has a
                                   trailing space, "OA ", kept as-is)
    RA    Review Application
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from parse_ccms_xml import (
    parse_ccms_xml_by_division,
    sum_parsed,
    sum_officer_rows,
    normalize_name,
    COLUMN_LABELS,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
RAW_DIR = DATA_DIR / "raw"
PUBLIC_DIR = ROOT / "public"

REPORT_URL = "https://ccms.karnataka.gov.in/ccms/StateReport.aspx"

ALL_DEPARTMENTS_VALUE = "0"  # ddldeptname "--All--" option

# Fixed report scope: Forest, Ecology & Environment dept, Non-secretariat
# (rblstatereport=2 => "Non Secretariat Department", matches the sample
# report the user demonstrated).
# Which secretariat department to pull. "FE" = Forest, Ecology and
# Environment. The dropdown also offers "--All--" (value "0"), which
# returns EVERY department in the state -- Agriculture, Animal Husbandry,
# Revenue and the rest -- in the same report. Nothing in the parser or
# dashboard is forest-specific, so this is the only line that decides
# scope.
#
#     CCMS_DEPT=FE  python3 scrape_ccms.py     # forest only (default)
#     CCMS_DEPT=0   python3 scrape_ccms.py     # every department
#
SECRETARIAT_DEPT = os.environ.get("CCMS_DEPT", "FE").strip()

BASE_FILTERS = {
    "rblstatereport": "2",
    "ddlsecdeptname": SECRETARIAT_DEPT,
}

# Every (court, bench, case type) combination to pull and sum per division.
HC = "High Court of Karnataka"
KSAT = "Karnataka State Administrative Tribunal"

COURT_CASE_TYPE_COMBOS = [
    {"ddlcourtname": "1", "ddlBench": "-All-", "ddlCasetype": "WP",
     "label": "Writ Petition", "court": HC},
    {"ddlcourtname": "1", "ddlBench": "-All-", "ddlCasetype": "WA",
     "label": "Writ Appeal", "court": HC},
    {"ddlcourtname": "1", "ddlBench": "-All-", "ddlCasetype": "CCC",
     "label": "Civil Contempt Petition", "court": HC},
    {"ddlcourtname": "1", "ddlBench": "-All-", "ddlCasetype": "S-KSAT",
     "label": "S-KSAT", "court": HC},
    {"ddlcourtname": "3", "ddlBench": None, "ddlCasetype": "OA ",
     "label": "Original Application", "court": KSAT},
    {"ddlcourtname": "3", "ddlBench": None, "ddlCasetype": "CA",
     "label": "Contempt Application", "court": KSAT},
    {"ddlcourtname": "3", "ddlBench": None, "ddlCasetype": "MA",
     "label": "Miscellaneous Application", "court": KSAT},
    {"ddlcourtname": "3", "ddlBench": None, "ddlCasetype": "RA",
     "label": "Review Application", "court": KSAT},
]

IST = timezone(timedelta(hours=5, minutes=30))

# Exact selectors, confirmed against the live page (inspect run
# 2026-08-11). These are tried first; the keyword scoring below is kept
# only as a fallback in case the page markup changes.
#
#   <input type="submit" id="btnview" name="btnview" value="View Report">
#   <a id="ReportViewer1_ctl05_ctl04_ctl00_ButtonLink" title="Export drop down menu">
#   <a title="XML file with report data">   (hidden until the menu opens)
#
# Note the page uses Select2 (Select/select2.css), which is why the
# native <select> elements report as hidden -- see _select_aspnet.
VIEW_REPORT_SELECTOR = "input#btnview"
EXPORT_MENU_SELECTOR = "a#ReportViewer1_ctl05_ctl04_ctl00_ButtonLink"
EXPORT_XML_SELECTOR = "a[title='XML file with report data']"
REPORT_VIEWER_ID = "ReportViewer1"

# Words that suggest a control runs/refreshes the report, and words that
# rule one out. Scored against a control's id / name / value / text /
# title rather than matched against one exact label. Used only if the
# exact selectors above stop matching.
VIEW_REPORT_POSITIVE = [
    "viewreport", "view report", "generatereport", "generate report",
    "showreport", "show report", "getreport", "get report",
    "btnview", "btnreport", "btnsearch", "btnsubmit", "btngo", "btnshow",
    "view", "report", "search", "submit", "generate", "show", "go", "ok",
]
VIEW_REPORT_NEGATIVE = [
    "reset", "clear", "cancel", "back", "logout", "log out", "signout",
    "sign out", "home", "print", "export", "help", "close", "exit",
    "refresh", "menu", "login",
]

EXPORT_POSITIVE = ["export", "save as", "download", "select a format"]
EXPORT_NEGATIVE = ["import", "reset", "cancel", "close", "logout"]

EXPORT_XML_POSITIVE = ["xml file with report data", "xml"]
EXPORT_XML_NEGATIVE = ["excel", "pdf", "word", "csv", "mhtml", "tiff", "powerpoint"]


def load_divisions() -> list[dict]:
    """The divisions to show on the dashboard.

    Normally a hand-picked list (divisions.json). Set CCMS_TRACK_ALL=1 to
    instead track every department the reports return -- useful when
    scaling past a single circle, since it needs no maintenance as
    divisions are added or renamed upstream.
    """
    if os.environ.get("CCMS_TRACK_ALL", "").strip() in ("1", "true", "yes"):
        return []  # populated from the reports themselves -- see run_scrape
    return json.loads((HERE / "divisions.json").read_text(encoding="utf-8"))


def _divisions_from_reports(reports: dict) -> list[dict]:
    """Build the division list from whatever the reports actually
    contained, for CCMS_TRACK_ALL mode."""
    names = set()
    for by_division in reports.values():
        names.update(by_division.keys())
    out = []
    for i, name in enumerate(sorted(names)):
        low = name.lower()
        group = "circle" if "circle" in low else ("sf" if low.endswith(" sf") or " sf " in low else "division")
        out.append({"code": f"AUTO{i:04d}", "name": name, "group": group})
    return out


def _wait_for_form_ready(
    page, timeout_ms: int = 45000, context: str = "", min_selects: int = 1
) -> None:
    """Wait until the report page has actually rendered.

    Every dropdown fires a full ASP.NET postback that replaces the whole
    document. Immediately after one the DOM is a bare <head> shell
    (confirmed live: `elements=14 inputs=0 selects=0`), so the script used
    to race ahead and find nothing to click. Waiting on "networkidle"
    alone was not enough.

    min_selects matters because the page is progressive: the LANDING page
    has no dropdowns at all -- only the "Within Secretariat / Outside
    Secretariat" radio (confirmed live: `elements=60 inputs=8 selects=0`,
    body text "Main Report Home Within Secretariat Outside Secretariat").
    The dropdowns are rendered by the server only after that radio is
    picked. So pass min_selects=0 for the initial load and 1 afterwards.
    """
    try:
        page.wait_for_function(
            """(minSelects) => {
                if (!document.body) return false;
                if (document.getElementsByTagName('*').length <= 40) return false;
                return document.getElementsByTagName('select').length >= minSelects;
            }""",
            arg=min_selects,
            timeout=timeout_ms,
        )
    except Exception:
        raise RuntimeError(
            f"page never finished rendering{' after ' + context if context else ''} "
            f"({timeout_ms}ms, needed >={min_selects} select(s)). "
            f"Current state:\n{_page_summary(page)}"
        ) from None


def _select_aspnet(page, field_name: str, value: str, settle_ms: int = 900) -> None:
    """Select an option in an ASP.NET dropdown that may be hidden.

    Confirmed live: these selects render as
        <select tabindex="-1" id="ddlsecdeptname" name="ddlsecdeptname"
                class="form-control ..." onchange="...__doPostBack...">
    and Playwright reports "element is not visible" -- the native <select>
    is hidden behind a JS dropdown widget (the tabindex="-1" is the
    giveaway). Playwright's normal select_option refuses to act on a
    non-visible element and times out after 30s.

    So: try the normal path, then force (skips the visibility check), then
    fall back to setting .value in JS and dispatching a 'change' event --
    which is what fires the page's onchange -> __doPostBack handler and
    makes the server repopulate the dependent dropdowns.
    """
    sel = f'select[name="{field_name}"]'
    page.wait_for_selector(sel, state="attached", timeout=20000)

    errors = []
    for attempt in ("normal", "force"):
        try:
            page.select_option(sel, value, timeout=4000, force=(attempt == "force"))
            break
        except Exception as exc:
            errors.append(f"{attempt}: {type(exc).__name__}")
    else:
        # Both Playwright paths failed -- drive it directly in the page.
        ok = page.evaluate(
            """([name, value]) => {
                const el = document.getElementsByName(name)[0];
                if (!el) return 'no element named ' + name;
                const match = Array.from(el.options).find(o => o.value === value);
                if (!match) {
                    return 'no option with value ' + JSON.stringify(value) +
                           '; available: ' +
                           Array.from(el.options).map(o => JSON.stringify(o.value)).join(', ');
                }
                el.value = value;
                el.dispatchEvent(new Event('input',  { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }""",
            [field_name, value],
        )
        if ok is not True:
            raise RuntimeError(
                f"could not set {field_name}={value!r} ({'; '.join(errors)}); JS fallback said: {ok}"
            )

    # Selecting fires __doPostBack, which replaces the whole document.
    # Wait for the form to actually come back before doing anything else.
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    _wait_for_form_ready(page, context=f"selecting {field_name}={value!r}")
    page.wait_for_timeout(settle_ms)


def _check_aspnet_radio(page, field_name: str, value: str, settle_ms: int = 700) -> None:
    """Same idea as _select_aspnet, for a radio button that may be hidden
    behind styled markup."""
    sel = f'input[name="{field_name}"][value="{value}"]'
    page.wait_for_selector(sel, state="attached", timeout=20000)

    try:
        page.check(sel, timeout=4000)
    except Exception:
        try:
            page.check(sel, timeout=4000, force=True)
        except Exception:
            ok = page.evaluate(
                """([name, value]) => {
                    const el = Array.from(document.getElementsByName(name))
                        .find(e => e.value === value);
                    if (!el) return 'no radio ' + name + '=' + value;
                    el.checked = true;
                    el.dispatchEvent(new Event('click',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }""",
                [field_name, value],
            )
            if ok is not True:
                raise RuntimeError(f"could not check {field_name}={value!r}; JS fallback said: {ok}")

    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    _wait_for_form_ready(page, context=f"checking {field_name}={value!r}")
    page.wait_for_timeout(settle_ms)


_COLLECT_CLICKABLES_JS = """(framePrefix) => {
    // Deliberately broad: ASP.NET pages submit from <input type=submit>,
    // <input type=image>, <button>, <a href="javascript:__doPostBack...">,
    // and sometimes plain <div>/<span> with an onclick handler.
    const sels = [
        'input', 'button', 'a', '[role=button]', '[role=menuitem]',
        '[onclick]', '[href]'
    ];
    const seen = new Set();
    const out = [];
    let idx = 0;
    // Gather candidates from CSS selectors AND from every form's own
    // element list -- ASP.NET submit buttons are always form elements,
    // so this catches anything the selectors above miss.
    const pool = [];
    for (const s of sels) {
        let nodes;
        try { nodes = document.querySelectorAll(s); } catch (e) { continue; }
        for (const n of nodes) pool.push(n);
    }
    try {
        for (const form of document.forms) {
            for (const el of form.elements) pool.push(el);
        }
    } catch (e) {}
    {
        for (const el of pool) {
            if (seen.has(el)) continue;
            seen.add(el);
            const tag = el.tagName.toLowerCase();
            const type = (el.getAttribute('type') || '').toLowerCase();
            // skip pure data-entry fields, keep anything clickable
            if (tag === 'input' && ['text','password','hidden','number','date','email','file','search','tel','url'].includes(type)) {
                continue;
            }
            let visible = false;
            try {
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                visible = !!(r.width || r.height)
                    && cs.visibility !== 'hidden'
                    && cs.display !== 'none'
                    && cs.opacity !== '0';
            } catch (e) {}
            const marker = framePrefix + '-' + idx;
            try { el.setAttribute('data-scrape-idx', marker); } catch (e) {}
            out.push({
                idx: marker,
                tag: tag,
                type: type,
                id: el.id || '',
                name: el.getAttribute('name') || '',
                value: el.getAttribute('value') || '',
                title: el.getAttribute('title') || '',
                alt: el.getAttribute('alt') || '',
                href: (el.getAttribute('href') || '').slice(0, 90),
                text: (el.innerText || el.textContent || '').trim().slice(0, 80),
                visible: visible
            });
            idx++;
        }
    }
    return out;
}"""

_PAGE_SUMMARY_JS = """() => ({
    url: location.href,
    title: document.title,
    totalElements: document.getElementsByTagName('*').length,
    inputs: document.getElementsByTagName('input').length,
    selects: document.getElementsByTagName('select').length,
    buttons: document.getElementsByTagName('button').length,
    anchors: document.getElementsByTagName('a').length,
    iframes: document.getElementsByTagName('iframe').length,
    bodyText: (document.body ? document.body.innerText : '').trim().slice(0, 600)
})"""


def _collect_clickables(page):
    """Collect clickable controls across the main document AND every
    iframe (ReportViewer frequently renders inside one).

    Returns (controls, frame_errors). Errors are returned rather than
    swallowed -- an empty list used to be indistinguishable from "the JS
    blew up", which made failures impossible to diagnose.
    """
    controls = []
    errors = []
    for i, frame in enumerate(page.frames):
        try:
            got = frame.evaluate(_COLLECT_CLICKABLES_JS, f"f{i}") or []
            for c in got:
                c["frame"] = i
                c["frame_url"] = (frame.url or "")[:90]
            controls.extend(got)
        except Exception as exc:
            errors.append(f"frame {i} ({(frame.url or '')[:60]}): {type(exc).__name__}: {exc}")
    return controls, errors


def _page_summary(page) -> str:
    """Human-readable snapshot of what the page actually contains right
    now -- the thing you need when a selector finds nothing."""
    lines = []
    for i, frame in enumerate(page.frames):
        try:
            s = frame.evaluate(_PAGE_SUMMARY_JS)
            lines.append(
                f"    frame {i}: url={s['url'][:80]!r} title={s['title'][:50]!r}\n"
                f"      elements={s['totalElements']} inputs={s['inputs']} "
                f"selects={s['selects']} buttons={s['buttons']} anchors={s['anchors']} "
                f"iframes={s['iframes']}"
            )
            if s["bodyText"]:
                preview = " ".join(s["bodyText"].split())[:300]
                lines.append(f"      body text: {preview!r}")
        except Exception as exc:
            lines.append(f"    frame {i}: could not inspect ({type(exc).__name__}: {exc})")
    return "\n".join(lines) if lines else "    (no frames)"


def _haystack(c: dict) -> str:
    return " ".join(
        str(c.get(k, "")) for k in ("id", "name", "value", "title", "alt", "text")
    ).lower()


def _score_control(c: dict, positives: list[str], negatives: list[str]) -> int:
    hay = _haystack(c)
    if not hay.strip():
        return -1
    for bad in negatives:
        if bad in hay:
            return -1
    score = 0
    for i, good in enumerate(positives):
        if good in hay:
            # earlier entries in the positives list are stronger signals
            score = max(score, len(positives) - i)
    if score and c.get("visible"):
        score += 100  # strongly prefer something the user could actually click
    return score


def _describe_controls(controls, limit: int = 60) -> str:
    lines = []
    for c in controls[:limit]:
        bits = [f"<{c['tag']}" + (f" type={c['type']}" if c.get("type") else "") + ">"]
        for k in ("id", "name", "value", "title", "text", "href"):
            if c.get(k):
                bits.append(f"{k}={c[k]!r}")
        bits.append("visible" if c.get("visible") else "hidden")
        if c.get("frame"):
            bits.append(f"[frame {c['frame']}]")
        lines.append("    " + " ".join(bits))
    if len(controls) > limit:
        lines.append(f"    ... and {len(controls) - limit} more")
    return "\n".join(lines) if lines else "    (none found)"


def _click_exact(page, selector: str, timeout_ms: int = 8000) -> bool:
    """Click a known selector, tolerating the element being hidden (the
    ReportViewer export menu items are display:none until the menu
    opens). Returns False rather than raising so callers can fall back."""
    try:
        page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    except Exception:
        return False
    for kwargs in ({"timeout": 3000}, {"timeout": 3000, "force": True}):
        try:
            page.click(selector, **kwargs)
            return True
        except Exception:
            pass
    try:
        return bool(
            page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    el.click();
                    return true;
                }""",
                selector,
            )
        )
    except Exception:
        return False


def _click_scored(page, positives, negatives, what: str) -> bool:
    """Find the best-matching clickable control and click it, falling back
    to a JS click so hidden/overlaid controls still work.

    Raises with a full dump of the page (controls + per-frame summary) if
    nothing scores -- that dump is what makes this debuggable remotely.
    """
    controls, frame_errors = _collect_clickables(page)
    scored = [(c, _score_control(c, positives, negatives)) for c in controls]
    scored = [(c, s) for c, s in scored if s > 0]
    scored.sort(key=lambda cs: -cs[1])

    if not scored:
        msg = [f"couldn't find a control for {what}."]
        msg.append(f"  Clickable controls found ({len(controls)}):")
        msg.append(_describe_controls(controls))
        if frame_errors:
            msg.append("  Frame errors (this is likely the real problem):")
            for e in frame_errors:
                msg.append(f"    {e}")
        msg.append("  Page state:")
        msg.append(_page_summary(page))
        raise RuntimeError("\n".join(msg))

    for c, _score in scored[:6]:
        frame = page.frames[c.get("frame", 0)]
        sel = f'[data-scrape-idx="{c["idx"]}"]'
        for attempt in ("normal", "force"):
            try:
                frame.click(sel, timeout=4000, force=(attempt == "force"))
                return True
            except Exception:
                pass
        try:
            clicked = frame.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    el.click();
                    return true;
                }""",
                sel,
            )
            if clicked:
                return True
        except Exception:
            continue

    tried = ", ".join(
        str(c.get("id") or c.get("name") or c.get("text") or c["tag"]) for c, _ in scored[:6]
    )
    raise RuntimeError(
        f"found candidates for {what} but none were clickable (tried: {tried}).\n"
        f"  All clickable controls:\n{_describe_controls(controls)}\n"
        f"  Page state:\n{_page_summary(page)}"
    )


def scrape_all_departments(page, combo: dict, out_xml_path: Path) -> None:
    """Pull one report for ddldeptname=--All-- (every circle/division
    under Forest, Ecology & Environment) for a given (court, case type)
    combo, and save its XML export."""
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
    _wait_for_form_ready(page, context="initial page load", min_selects=0)

    # Order matters: the rblstatereport radio ("Secretariat" vs "Non
    # Secretariat Department") drives which filter dropdowns the server
    # renders, so it goes first and its postback must settle before we
    # touch ddlsecdeptname.
    _check_aspnet_radio(page, "rblstatereport", BASE_FILTERS["rblstatereport"])

    # Forest, Ecology and Environment
    _select_aspnet(page, "ddlsecdeptname", BASE_FILTERS["ddlsecdeptname"])

    # --All-- units/divisions under that department
    _select_aspnet(page, "ddldeptname", ALL_DEPARTMENTS_VALUE)

    # Court must be selected BEFORE case type -- case type options
    # repopulate depending on the chosen court (dependent dropdown).
    _select_aspnet(page, "ddlcourtname", combo["ddlcourtname"])

    if combo.get("ddlBench") is not None:
        try:
            _select_aspnet(page, "ddlBench", combo["ddlBench"], settle_ms=300)
        except Exception:
            pass  # bench filter may not apply to this court (e.g. KSAT)

    _select_aspnet(page, "ddlCasetype", combo["ddlCasetype"], settle_ms=300)

    label = combo_label(combo)

    # Make sure the form is fully back before hunting for the button --
    # this is the step that used to run against a bare <head> shell.
    _wait_for_form_ready(page, context="applying filters")

    # Clicking "View Report" is what actually renders the report and its
    # export toolbar -- nothing exists to export before this. The report
    # renders in the same tab (confirmed: no popup).
    if not _click_exact(page, VIEW_REPORT_SELECTOR):
        try:
            _click_scored(page, VIEW_REPORT_POSITIVE, VIEW_REPORT_NEGATIVE, "'View Report'")
        except RuntimeError as exc:
            raise RuntimeError(f"[{label}] {exc}") from None

    try:
        page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    _wait_for_form_ready(page, context="clicking View Report")
    page.wait_for_timeout(3000)  # report render + postback settle

    # Grab the column headings off the rendered report before exporting --
    # the XML export does not include them.
    headers = _extract_report_headers(page, label)

    _export_report_xml(page, label, out_xml_path)
    return headers


def _looks_like_report_xml(text: str) -> bool:
    return bool(text) and "<Report" in text[:4000]


def _capture_export(page, trigger_fn, out_xml_path: Path, timeout_ms: int = 45000) -> str:
    """Run trigger_fn and capture the exported XML however it arrives.

    The ReportViewer export is JavaScript-driven, so the file can come
    back three different ways depending on headers and browser policy:
      1. a real download (Content-Disposition: attachment)
      2. a new tab/window that renders the XML inline
      3. an XHR/navigation whose response body we can read off the wire

    Relying on Playwright's download event alone misses (2) and (3), so
    all three are watched simultaneously. Returns a short string naming
    whichever path produced the file.
    """
    context = page.context
    seen_responses = []

    def on_response(resp):
        try:
            url = resp.url
            low = url.lower()
            if "reportviewerwebcontrol.axd" in low or "format=xml" in low or "optype=export" in low:
                seen_responses.append(resp)
        except Exception:
            pass

    new_pages = []

    def on_page(p):
        new_pages.append(p)

    page.on("response", on_response)
    context.on("page", on_page)

    try:
        # --- 1. real download -----------------------------------------
        try:
            with page.expect_download(timeout=timeout_ms) as dl_info:
                trigger_fn()
            dl_info.value.save_as(str(out_xml_path))
            return "download event"
        except Exception:
            pass  # fall through -- the trigger already fired

        page.wait_for_timeout(2500)

        # --- 2. XML opened in a new tab -------------------------------
        for p in list(new_pages):
            try:
                p.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            for getter in (
                lambda: p.evaluate("() => document.documentElement.outerHTML"),
                lambda: p.content(),
            ):
                try:
                    text = getter()
                except Exception:
                    continue
                if _looks_like_report_xml(text):
                    out_xml_path.write_text(text, encoding="utf-8")
                    try:
                        p.close()
                    except Exception:
                        pass
                    return "new tab"
            try:
                p.close()
            except Exception:
                pass

        # --- 3. response body off the wire ----------------------------
        for resp in reversed(seen_responses):
            try:
                body = resp.text()
            except Exception:
                continue
            if _looks_like_report_xml(body):
                out_xml_path.write_text(body, encoding="utf-8")
                return f"network response ({resp.url[:60]})"

        # --- 4. the current page navigated to the XML -----------------
        try:
            text = page.evaluate("() => document.documentElement.outerHTML")
            if _looks_like_report_xml(text):
                out_xml_path.write_text(text, encoding="utf-8")
                page.go_back(wait_until="networkidle")
                return "same-tab navigation"
        except Exception:
            pass

        raise RuntimeError(
            "export was triggered but no XML arrived "
            f"(saw {len(seen_responses)} candidate response(s), {len(new_pages)} new tab(s))"
        )
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        try:
            context.remove_listener("page", on_page)
        except Exception:
            pass


_EXTRACT_HEADERS_JS = """() => {
    // The XML export carries no column headings -- only the rendered
    // SSRS report does. Walk the ReportViewer's table rows and pick the
    // row that looks like the header: mostly non-numeric text cells, and
    // containing the one heading we know appears in every report.
    const rv = document.getElementById('ReportViewer1');
    if (!rv) return { error: 'no #ReportViewer1 on page' };

    const rows = Array.from(rv.querySelectorAll('tr'));
    const candidates = [];
    for (const tr of rows) {
        const cells = Array.from(tr.children)
            .filter(c => c.tagName === 'TD' || c.tagName === 'TH')
            .map(c => (c.innerText || c.textContent || '').replace(/\\s+/g, ' ').trim());
        const nonEmpty = cells.filter(t => t.length > 0);
        if (nonEmpty.length < 3) continue;
        const numeric = nonEmpty.filter(t => /^-?\\d+(\\.\\d+)?$/.test(t)).length;
        const wordy = nonEmpty.filter(t => /[A-Za-z]{3,}/.test(t)).length;
        candidates.push({ cells: cells, nonEmpty: nonEmpty, numeric: numeric, wordy: wordy });
    }

    // Strong signal: the row that mentions the pending-cases heading.
    let bestIdx = candidates.findIndex(c =>
        c.nonEmpty.some(t => /total\\s+cases\\s+pending/i.test(t)));

    // Otherwise: the wordiest, least numeric row.
    if (bestIdx < 0) {
        let best = candidates
            .filter(c => c.numeric === 0 && c.wordy >= 4)
            .sort((a, b) => b.wordy - a.wordy)[0];
        bestIdx = best ? candidates.indexOf(best) : -1;
    }
    if (bestIdx < 0) return { error: 'no header-like row found', rowsScanned: candidates.length };

    // Some reports put group headings (e.g. "Completed Case") on the main
    // row and their sub-column headings on the row below, so capture the
    // next couple of rows too -- without them a grouped report yields
    // fewer headings than it has columns and cannot be mapped.
    const following = [];
    for (let i = bestIdx + 1; i < Math.min(bestIdx + 4, candidates.length); i++) {
        following.push(candidates[i].nonEmpty);
    }

    return {
        headers: candidates[bestIdx].nonEmpty,
        raw: candidates[bestIdx].cells,
        followingRows: following,
        rowsScanned: candidates.length
    };
}"""


def _extract_report_headers(page, label: str):
    """Best-effort scrape of the rendered report's column headings.

    Returns {"headers": [...], "raw": [...]} or {"error": "..."}. Stored
    in the snapshot so the dashboard can show real column names for
    reports whose layout differs from the 17-column Writ Petition one
    (Civil Contempt has 20 columns, Writ Appeal and S-KSAT have 16).
    """
    try:
        result = page.evaluate(_EXTRACT_HEADERS_JS)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    if isinstance(result, dict) and result.get("headers"):
        print(
            f"  [{label}] captured {len(result['headers'])} column heading(s)",
            file=sys.stderr,
        )
    else:
        print(f"  [{label}] could not capture column headings: {result}", file=sys.stderr)
    return result


def _export_report_xml(page, label: str, out_xml_path: Path) -> None:
    """Export the rendered report as 'XML file with report data'.

    The export is driven by the ReportViewer's JavaScript -- the toolbar
    "save"/export control opens a menu whose items are
    <a href="javascript:void(0)">, so there is no plain URL to fetch.
    Three ways to trigger it are tried in order, and _capture_export
    handles whichever way the resulting file comes back.
    """
    attempts = []

    def _trigger_client_api():
        result = page.evaluate(
            """(viewerId) => {
                if (typeof $find !== 'function') return 'no $find on page';
                const ids = [];
                if (typeof Sys !== 'undefined' && Sys.Application
                    && Sys.Application._components) {
                    for (const k in Sys.Application._components) ids.push(k);
                }
                ids.push(viewerId);
                for (const id of ids) {
                    let v = null;
                    try { v = $find(id); } catch (e) { continue; }
                    if (v && typeof v.exportReport === 'function') {
                        v.exportReport('XML');
                        return true;
                    }
                }
                return 'no ReportViewer with exportReport(); tried: ' + ids.join(',');
            }""",
            REPORT_VIEWER_ID,
        )
        if result is not True:
            raise RuntimeError(str(result))

    def _trigger_exact_menu():
        if not _click_exact(page, EXPORT_MENU_SELECTOR):
            raise RuntimeError(f"export menu {EXPORT_MENU_SELECTOR} not clickable")
        page.wait_for_timeout(1000)  # let the menu render
        if not _click_exact(page, EXPORT_XML_SELECTOR):
            raise RuntimeError(f"XML item {EXPORT_XML_SELECTOR} not clickable")

    def _trigger_scored():
        _click_scored(page, EXPORT_POSITIVE, EXPORT_NEGATIVE, "'Export'")
        page.wait_for_timeout(1000)
        _click_scored(page, EXPORT_XML_POSITIVE, EXPORT_XML_NEGATIVE, "'XML file with report data'")

    for name, trigger in (
        ("ReportViewer client API", _trigger_client_api),
        ("exact toolbar selectors", _trigger_exact_menu),
        ("keyword scoring", _trigger_scored),
    ):
        try:
            how = _capture_export(page, trigger, out_xml_path)
            print(f"  [{label}] exported via {name} ({how})", file=sys.stderr)
            return
        except Exception as exc:
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
            attempts.append(f"{name}: {first}")

    raise RuntimeError(
        f"[{label}] could not export the report. Attempts:\n    " + "\n    ".join(attempts)
    )


def combo_label(combo: dict) -> str:
    return f"court{combo['ddlcourtname']}_{combo['ddlCasetype'].strip()}"


def capture_headers(only: list[str] | None = None) -> None:
    """`python3 scrape_ccms.py --headers [CODE ...]`

    Visits each report, reads its column headings off the rendered page,
    prints them, and saves them to data/report_headers.json. The XML
    export contains no headings, so this is the only way to label the
    columns of reports whose layout differs from the 17-column Writ
    Petition one (Civil Contempt has 20 columns, Writ Appeal and S-KSAT
    have 16).

    Saved separately from the daily snapshot on purpose: headings change
    only when the report definition changes, so capturing them once is
    enough, and keeping them in their own file means a snapshot rebuild
    can never lose them.

        python3 scrape_ccms.py --headers            # all case types
        python3 scrape_ccms.py --headers CCC WA     # just these
    """
    from playwright.sync_api import sync_playwright

    headless = os.environ.get("HEADLESS", "true").lower() != "false"
    out_path = DATA_DIR / "report_headers.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    combos = COURT_CASE_TYPE_COMBOS
    if only:
        wanted = {c.strip().upper() for c in only}
        combos = [c for c in combos if c["ddlCasetype"].strip().upper() in wanted]
        if not combos:
            print(f"No case type matches {sorted(wanted)}", file=sys.stderr)
            return

    tmp_dir = RAW_DIR / "_headers"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        for combo in combos:
            label = combo_label(combo)
            print(f"\n=== {combo['label']} ({combo['court']}) ===", file=sys.stderr)
            try:
                page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
                _wait_for_form_ready(page, context="initial page load", min_selects=0)
                _check_aspnet_radio(page, "rblstatereport", BASE_FILTERS["rblstatereport"])
                _select_aspnet(page, "ddlsecdeptname", BASE_FILTERS["ddlsecdeptname"])
                _select_aspnet(page, "ddldeptname", ALL_DEPARTMENTS_VALUE)
                _select_aspnet(page, "ddlcourtname", combo["ddlcourtname"])
                if combo.get("ddlBench") is not None:
                    try:
                        _select_aspnet(page, "ddlBench", combo["ddlBench"], settle_ms=300)
                    except Exception:
                        pass
                _select_aspnet(page, "ddlCasetype", combo["ddlCasetype"], settle_ms=300)
                _wait_for_form_ready(page, context="applying filters")

                if not _click_exact(page, VIEW_REPORT_SELECTOR):
                    _click_scored(page, VIEW_REPORT_POSITIVE, VIEW_REPORT_NEGATIVE, "'View Report'")
                try:
                    page.wait_for_load_state("networkidle", timeout=60000)
                except Exception:
                    pass
                _wait_for_form_ready(page, context="clicking View Report")
                page.wait_for_timeout(3000)

                result = page.evaluate(_EXTRACT_HEADERS_JS)
                headers = result.get("headers") if isinstance(result, dict) else None
                if headers:
                    existing[label] = {
                        "label": combo["label"],
                        "court": combo["court"],
                        "headers": headers,
                        "raw": result.get("raw"),
                        "followingRows": result.get("followingRows"),
                    }
                    print(f"  captured {len(headers)} heading(s):", file=sys.stderr)
                    for i, h in enumerate(headers):
                        print(f"    {i:2}. {h}", file=sys.stderr)
                else:
                    print(f"  FAILED: {result}", file=sys.stderr)
            except Exception as exc:
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
                print(f"  FAILED: {first}", file=sys.stderr)

        browser.close()

    out_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved to {out_path}", file=sys.stderr)
    print("Now run:  python3 build_dashboard_data.py", file=sys.stderr)


def inspect_page() -> None:
    """Diagnostic mode: `python3 scrape_ccms.py --inspect`

    Loads the report page, applies the filters exactly as a real run
    would, then dumps every clickable control and a per-frame summary --
    once, instead of repeating the same failure for all 7 combos. Use
    this to find out what the 'View Report' / 'Export' controls actually
    are, then paste the output.
    """
    from playwright.sync_api import sync_playwright

    headless = os.environ.get("HEADLESS", "true").lower() != "false"
    combo = COURT_CASE_TYPE_COMBOS[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        # accept_downloads must be on for expect_download to ever fire.
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        print("=== loading report page ===", file=sys.stderr)
        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
        _wait_for_form_ready(page, context="initial page load", min_selects=0)

        steps = [
            ("rblstatereport (radio)", lambda: _check_aspnet_radio(
                page, "rblstatereport", BASE_FILTERS["rblstatereport"])),
            ("ddlsecdeptname = FE", lambda: _select_aspnet(
                page, "ddlsecdeptname", BASE_FILTERS["ddlsecdeptname"])),
            ("ddldeptname = --All--", lambda: _select_aspnet(
                page, "ddldeptname", ALL_DEPARTMENTS_VALUE)),
            ("ddlcourtname", lambda: _select_aspnet(
                page, "ddlcourtname", combo["ddlcourtname"])),
            ("ddlBench", lambda: _select_aspnet(
                page, "ddlBench", combo["ddlBench"], settle_ms=300)),
            ("ddlCasetype", lambda: _select_aspnet(
                page, "ddlCasetype", combo["ddlCasetype"], settle_ms=300)),
        ]
        for name, fn in steps:
            try:
                fn()
                print(f"  OK   {name}", file=sys.stderr)
            except Exception as exc:
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
                print(f"  FAIL {name}: {first}", file=sys.stderr)

        _wait_for_form_ready(page, context="applying filters")
        page.wait_for_timeout(1000)

        # Try the actual View Report click, since the report (and its
        # export toolbar) only exists afterwards.
        print("\n=== clicking 'View Report' ===", file=sys.stderr)
        try:
            if _click_exact(page, VIEW_REPORT_SELECTOR):
                print(f"  clicked {VIEW_REPORT_SELECTOR} OK", file=sys.stderr)
            else:
                _click_scored(page, VIEW_REPORT_POSITIVE, VIEW_REPORT_NEGATIVE, "'View Report'")
                print("  clicked via scoring fallback", file=sys.stderr)
            try:
                page.wait_for_load_state("networkidle", timeout=60000)
            except Exception:
                pass
            _wait_for_form_ready(page, context="clicking View Report")
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)

        # Prove the export actually produces a file.
        print("\n=== testing XML export ===", file=sys.stderr)
        test_xml = RAW_DIR / "_inspect_test.xml"
        test_xml.parent.mkdir(parents=True, exist_ok=True)
        try:
            _export_report_xml(page, "inspect", test_xml)
            size = test_xml.stat().st_size if test_xml.exists() else 0
            print(f"  EXPORT OK -> {test_xml} ({size} bytes)", file=sys.stderr)
            try:
                divs = parse_ccms_xml_by_division(test_xml)
                print(f"  parsed {len(divs)} department rows:", file=sys.stderr)
                for name in sorted(divs)[:15]:
                    print(
                        f"    {name} -> pending={divs[name]['totals']['total_cases_pending']}",
                        file=sys.stderr,
                    )
                if len(divs) > 15:
                    print(f"    ... and {len(divs) - 15} more", file=sys.stderr)
            except Exception as exc:
                print(f"  (could not parse: {exc})", file=sys.stderr)
        except Exception as exc:
            print(f"  EXPORT FAILED: {exc}", file=sys.stderr)

        print("\n=== PAGE STATE ===", file=sys.stderr)
        print(_page_summary(page), file=sys.stderr)

        controls, frame_errors = _collect_clickables(page)
        print(f"\n=== CLICKABLE CONTROLS ({len(controls)}) ===", file=sys.stderr)
        print(_describe_controls(controls, limit=200), file=sys.stderr)

        if frame_errors:
            print("\n=== FRAME ERRORS ===", file=sys.stderr)
            for e in frame_errors:
                print(f"    {e}", file=sys.stderr)

        print("\n=== BEST GUESSES ===", file=sys.stderr)
        for what, pos, neg in (
            ("View Report", VIEW_REPORT_POSITIVE, VIEW_REPORT_NEGATIVE),
            ("Export", EXPORT_POSITIVE, EXPORT_NEGATIVE),
        ):
            ranked = sorted(
                ((c, _score_control(c, pos, neg)) for c in controls),
                key=lambda cs: -cs[1],
            )
            ranked = [(c, s) for c, s in ranked if s > 0][:3]
            if ranked:
                print(f"  {what}:", file=sys.stderr)
                for c, s in ranked:
                    ident = c.get("id") or c.get("name") or c.get("text") or c["tag"]
                    print(f"    score={s} {ident!r} (value={c.get('value')!r})", file=sys.stderr)
            else:
                print(f"  {what}: NO CANDIDATES", file=sys.stderr)

        if not headless:
            print(
                "\nBrowser stays open 30s so you can look at the page.",
                file=sys.stderr,
            )
            page.wait_for_timeout(30000)

        browser.close()


def run_scrape() -> dict:
    from playwright.sync_api import sync_playwright

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    divisions = load_divisions()
    track_all = not divisions  # CCMS_TRACK_ALL=1 -> derive from the reports

    today = datetime.now(IST).strftime("%Y-%m-%d")
    today_raw_dir = RAW_DIR / today
    today_raw_dir.mkdir(parents=True, exist_ok=True)

    headless = os.environ.get("HEADLESS", "true").lower() != "false"

    # code -> list of {"totals":..., "officers":...} dicts, one per combo
    # that actually contained that division (a division can legitimately
    # be absent from a combo's report if it has zero cases of that type).
    by_case_type: dict[str, dict] = {}  # combo label -> {division code: payload}
    report_headers: dict[str, dict] = {}  # combo label -> scraped column headings
    combo_errors: dict[str, str] = {}
    seen_names_by_combo: dict[str, set[str]] = {}
    parsed_reports: dict[str, dict] = {}  # combo label -> {dept name: payload}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        # accept_downloads must be on for expect_download to ever fire.
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        for combo in COURT_CASE_TYPE_COMBOS:
            label = combo_label(combo)
            xml_path = today_raw_dir / f"all_departments__{label}.xml"
            print(f"scraping combo {label} (--All-- departments) ...", file=sys.stderr)
            try:
                headers = scrape_all_departments(page, combo, xml_path)
                if isinstance(headers, dict) and headers.get("headers"):
                    report_headers[label] = headers
                by_division = parse_ccms_xml_by_division(xml_path)
            except Exception as exc:
                combo_errors[label] = str(exc)
                print(f"  FAILED: {exc}", file=sys.stderr)
                continue

            parsed_reports[label] = by_division
            seen_names_by_combo[label] = set(by_division.keys())
            print(f"  {label}: report had {len(by_division)} department rows", file=sys.stderr)

        browser.close()

    # In track-all mode the division list comes from the reports
    # themselves, so it needs no upkeep as departments are added upstream.
    if track_all:
        divisions = _divisions_from_reports(parsed_reports)
        print(f"\nCCMS_TRACK_ALL: tracking {len(divisions)} departments found in the reports",
              file=sys.stderr)

    target_by_normalized_name = {normalize_name(d["name"]): d for d in divisions}
    per_division_combo_results: dict[str, list[dict]] = {d["code"]: [] for d in divisions}

    for label, by_division in parsed_reports.items():
        matched = 0
        for div_name, payload in by_division.items():
            target = target_by_normalized_name.get(normalize_name(div_name))
            if target is None:
                continue  # not a tracked department
            per_division_combo_results[target["code"]].append(payload)
            # Keep this case type's figures separate too, so the dashboard
            # can show one report at a time with its own (correct) columns
            # instead of mixing incompatible layouts.
            by_case_type.setdefault(label, {})[target["code"]] = payload
            matched += 1
        print(f"  {label}: {matched}/{len(by_division)} rows matched the tracked list",
              file=sys.stderr)

    # Fail fast: if not a single combo produced a usable report, do NOT
    # write a snapshot. Writing one here would record every division as
    # "no data" (or, before the sum_parsed fix, as a bogus 0) and would
    # become the baseline that tomorrow's increase/decrease is measured
    # against. Better to abort loudly and leave yesterday's good data in
    # place.
    if not seen_names_by_combo:
        print(
            f"\nABORTING: all {len(COURT_CASE_TYPE_COMBOS)} report combos failed -- "
            "no snapshot written, existing data left untouched.\n"
            "Errors were:",
            file=sys.stderr,
        )
        for label, msg in combo_errors.items():
            first_line = msg.strip().splitlines()[0] if msg.strip() else msg
            print(f"  {label}: {first_line}", file=sys.stderr)
        print(
            "\nRe-run with HEADLESS=false to watch the browser and see which "
            "step is failing.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    total_combos = len(COURT_CASE_TYPE_COMBOS)
    succeeded_combos = len(seen_names_by_combo)
    all_combos_ok = succeeded_combos == total_combos

    results = {}
    officers_by_code = {}
    for d in divisions:
        code = d["code"]
        combo_results = per_division_combo_results[code]
        totals_list = [r["totals"] for r in combo_results]
        officers_lists = [r["officers"] for r in combo_results]

        combined = sum_parsed(totals_list)

        # A division only appears in a report when it HAS cases of that
        # type (confirmed live: the Civil Contempt report listed 29 of the
        # ~120 departments). So "absent from every report" means a real
        # zero -- but only if every report actually came back. If any
        # combo failed we genuinely do not know, and must not print 0.
        if not totals_list:
            if all_combos_ok:
                combined["cases_received_as_on_yesterday"] = 0
                combined["total_cases_pending"] = 0
                combined["_zero_reason"] = "absent from all reports; all reports succeeded"
            else:
                combined["_unknown_reason"] = (
                    f"absent from all reports, but only {succeeded_combos}/{total_combos} "
                    "reports succeeded -- treating as unknown, not zero"
                )

        combined["combined_from_combos"] = len(totals_list)
        results[code] = combined
        officers_by_code[code] = sum_officer_rows(officers_lists)

        total = combined.get("total_cases_pending")
        print(
            f"{d['name']!r}: found in {len(totals_list)}/{total_combos} reports, "
            f"total_cases_pending = {total if total is not None else 'UNKNOWN'}",
            file=sys.stderr,
        )
        if not totals_list and not all_combos_ok:
            print(
                f"  WARNING: {d['name']!r} not found in any report AND "
                f"{total_combos - succeeded_combos} report(s) failed -- shown as no-data, "
                "not zero. Re-run once the failures are fixed.",
                file=sys.stderr,
            )

    snapshot = {
        "date": today,
        "generated_at": datetime.now(IST).isoformat(),
        "divisions": results,
        "officers": officers_by_code,
        "by_case_type": by_case_type,
        "divisions_meta": divisions,
        "report_headers": report_headers,
        "case_types": [
            {
                "key": combo_label(c),
                "label": c["label"],
                "court": c["court"],
                "case_type_code": c["ddlCasetype"].strip(),
            }
            for c in COURT_CASE_TYPE_COMBOS
        ],
        "combo_errors": combo_errors,
        "seen_department_names_by_combo": {k: sorted(v) for k, v in seen_names_by_combo.items()},
    }
    (SNAPSHOT_DIR / f"{today}.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if combo_errors:
        print(f"\n{len(combo_errors)}/{len(COURT_CASE_TYPE_COMBOS)} combo(s) failed to scrape:", file=sys.stderr)
        for label, msg in combo_errors.items():
            print(f"  {label}: {msg}", file=sys.stderr)

    return snapshot


if __name__ == "__main__":
    if "--headers" in sys.argv:
        i = sys.argv.index("--headers")
        capture_headers(sys.argv[i + 1:] or None)
    elif "--inspect" in sys.argv:
        inspect_page()
    else:
        run_scrape()
        # Rebuild the dashboard JSON from whatever snapshots exist on disk.
        from build_dashboard_data import build

        build()
