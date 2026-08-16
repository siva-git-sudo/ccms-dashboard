#!/usr/bin/env python3
"""
Cross-platform runner for the CCMS scrape (Windows / macOS / Linux).

The Windows equivalent of run_scrape.sh: runs the scraper headless, tees
everything to a timestamped log in logs/, keeps the last 30 logs, and
optionally commits + pushes the new snapshot so GitHub Pages republishes.

    python run_scrape.py             scrape only
    python run_scrape.py --push      scrape, then commit + push to git
    python run_scrape.py --visible   show the browser window (debugging)
    python run_scrape.py --all       every Forest division, not divisions.json

Exit codes: 0 = scrape succeeded, non-zero = scrape failed (nothing
published, previous data left intact).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SCRAPER_DIR = PROJECT_DIR / "scraper"
LOG_DIR = PROJECT_DIR / "logs"
KEEP_LOGS = 30

# Files the scrape regenerates and that must persist in the repo -- the
# dashboard's up/down arrows compare today's snapshot with the previous
# one, so the snapshot history has to be committed, not just the site.
COMMIT_PATHS = ["data/snapshots", "public/data.json", "public/data.js"]

SNAP_DIR = PROJECT_DIR / "data" / "snapshots"

# --- degraded-scrape thresholds -----------------------------------------
# scrape_ccms.py exits 0 when *some* of the eight reports come back, which
# is the right call for the scraper but the wrong one for publishing: on
# 2026-08-12 six reports returned zero rows and the seventh failed
# outright, and a 2,777 -> 113 collapse got committed and pushed. These
# thresholds are the publish gate that was missing.
MAX_TOTAL_DROP = 0.35   # a >35% fall day-on-day is a scrape fault, not news
COMBO_COLLAPSE_FLOOR = 10   # a report with >=10 cases never legitimately
                            # empties overnight; below that a real zero is
                            # plausible, so it is not treated as a fault


class Tee:
    """Write to the console and the log file at the same time."""

    def __init__(self, log_path: Path):
        self.stream = open(log_path, "w", encoding="utf-8", errors="replace")

    def write(self, text: str) -> None:
        sys.__stdout__.write(text)
        sys.__stdout__.flush()
        self.stream.write(text)
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


def log(tee: Tee, message: str = "") -> None:
    tee.write(message + "\n")


def run(tee: Tee, cmd: list[str], cwd: Path, env: dict | None = None) -> int:
    """Run a command, streaming its output into the log as it happens."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        tee.write(line)
    return proc.wait()


def capture(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run a command quietly and return (exit code, combined output)."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.strip()


def snapshot_totals(path: Path) -> tuple[int, dict]:
    """Total pending in a snapshot, plus the per-report breakdown."""
    data = json.loads(path.read_text(encoding="utf-8"))
    per = {}
    for combo, divisions in (data.get("by_case_type") or {}).items():
        per[combo] = sum(
            (d.get("totals") or {}).get("total_cases_pending") or 0
            for d in divisions.values()
        )
    return sum(per.values()), per


def verify_scrape(tee: Tee) -> bool:
    """Refuse to publish a scrape that lost most of its data.

    Returns True if the new snapshot looks sane. On failure the bad
    snapshot is quarantined and the regenerated site files are restored
    from git, so the working tree goes back to the last good state.
    """
    snaps = sorted(SNAP_DIR.glob("*.json"))
    if len(snaps) < 2:
        log(tee, "Sanity check skipped: no earlier snapshot to compare against.")
        return True

    new, prev = snaps[-1], snaps[-2]
    try:
        new_total, new_per = snapshot_totals(new)
        prev_total, prev_per = snapshot_totals(prev)
    except Exception as exc:
        log(tee, f"Sanity check could not read the snapshots: {exc}")
        return True

    log(tee)
    log(tee, "--- sanity check ---")
    log(tee, f"{prev.stem}: {prev_total:,} pending   ->   {new.stem}: {new_total:,} pending")

    problems = []

    if prev_total > 0:
        drop = (prev_total - new_total) / prev_total
        if drop > MAX_TOTAL_DROP:
            problems.append(
                f"total pending fell {drop*100:.0f}% "
                f"({prev_total:,} -> {new_total:,}), over the {MAX_TOTAL_DROP*100:.0f}% limit"
            )

    for combo, was in sorted(prev_per.items()):
        now = new_per.get(combo, 0)
        if was >= COMBO_COLLAPSE_FLOOR and now == 0:
            problems.append(f"{combo}: {was:,} cases yesterday, 0 today")

    if not problems:
        log(tee, "OK -- figures are within the expected range.")
        return True

    log(tee)
    log(tee, "!!! DEGRADED SCRAPE -- refusing to publish !!!")
    for p in problems:
        log(tee, "  - " + p)
    log(tee)

    # Quarantine rather than delete: the file is evidence of what the site
    # actually returned, and is worth keeping when diagnosing a failure.
    rejected = new.with_suffix(".json.rejected")
    try:
        if rejected.exists():
            rejected.unlink()
        new.rename(rejected)
        log(tee, f"Quarantined {new.name} -> {rejected.name}")
    except OSError as exc:
        log(tee, f"WARNING: could not quarantine {new.name}: {exc}")

    # The scrape already overwrote the site files, so put the committed
    # versions back. The dashboard keeps serving the last good day.
    if (PROJECT_DIR / ".git").exists() and shutil.which("git"):
        code, out = capture(
            ["git", "checkout", "--", "public/data.json", "public/data.js"], PROJECT_DIR
        )
        if code == 0:
            log(tee, "Restored public/data.json and public/data.js from the last commit.")
        else:
            log(tee, f"WARNING: could not restore the site files: {out}")

    log(tee)
    log(tee, "Nothing published. Check the scrape log above for the failing")
    log(tee, "report, then re-run. The CCMS site is often the cause -- a")
    log(tee, "retry a few minutes later usually comes back clean.")
    return False


def prune_logs() -> None:
    logs = sorted(
        LOG_DIR.glob("scrape_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in logs[KEEP_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass


def do_push(tee: Tee) -> None:
    """Commit the new snapshot and push. Never fails the run."""
    log(tee)
    log(tee, "--- committing and pushing to git ---")

    if not (PROJECT_DIR / ".git").exists():
        log(tee, f"WARNING: {PROJECT_DIR} is not a git repository; skipping push.")
        return

    if shutil.which("git") is None:
        log(tee, "WARNING: git not found on PATH; skipping push.")
        return

    # Stage only the scrape outputs. Unrelated edits in the working tree
    # (a half-finished change to the dashboard, say) stay uncommitted.
    existing = [p for p in COMMIT_PATHS if (PROJECT_DIR / p).exists()]
    if not existing:
        log(tee, "WARNING: no scrape outputs found to commit.")
        return
    capture(["git", "add", *existing], PROJECT_DIR)

    code, _ = capture(["git", "diff", "--staged", "--quiet"], PROJECT_DIR)
    if code == 0:
        log(tee, "No data changes to commit.")
        return

    stamp = datetime.now().strftime("%Y-%m-%d")
    code, out = capture(
        [
            "git",
            "-c", "user.name=ccms-bot",
            "-c", "user.email=ccms-bot@users.noreply.github.com",
            "commit", "-m", f"CCMS snapshot {stamp}",
        ],
        PROJECT_DIR,
    )
    if code != 0:
        log(tee, f"WARNING: commit failed:\n{out}")
        return
    log(tee, "committed.")

    code, out = capture(["git", "push"], PROJECT_DIR)
    if code != 0:
        log(tee, f"WARNING: git push failed (check credentials / remote):\n{out}")
        log(tee, "The snapshot is committed locally -- 'git push' by hand once fixed.")
        return
    log(tee, "pushed. GitHub Pages will republish from this commit.")


def main() -> int:
    args = {a.lower() for a in sys.argv[1:]}
    want_push = "--push" in args
    visible = "--visible" in args
    track_all = "--all" in args

    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = LOG_DIR / f"scrape_{timestamp}.log"
    tee = Tee(log_file)

    python = sys.executable or "python"

    try:
        log(tee, f"=== CCMS scrape started {datetime.now():%Y-%m-%d %H:%M:%S} ===")
        log(tee, f"project: {PROJECT_DIR}")
        log(tee, f"python:  {python}")
        log(tee, f"log:     {log_file}")
        log(tee)

        if not SCRAPER_DIR.is_dir():
            log(tee, f"FATAL: scraper directory not found at {SCRAPER_DIR}")
            return 1

        env = os.environ.copy()
        env["HEADLESS"] = "false" if visible else "true"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        if track_all:
            env["CCMS_TRACK_ALL"] = "1"

        status = run(tee, [python, "scrape_ccms.py"], SCRAPER_DIR, env)

        log(tee)
        if status != 0:
            log(tee, f"=== SCRAPE FAILED (exit {status}) at {datetime.now():%H:%M:%S} ===")
            log(tee, "No snapshot written, nothing published. Previous data left intact.")
            return status

        log(tee, f"=== scrape OK at {datetime.now():%H:%M:%S} ===")

        # A zero exit from the scraper only means it did not crash. Whether
        # the data it produced is publishable is a separate question.
        if not verify_scrape(tee):
            log(tee, f"=== SANITY CHECK FAILED at {datetime.now():%H:%M:%S} ===")
            return 2

        # Gate-throughput scorecard for public/analytics.html. Additive: it
        # only appends `scorecard` and `stats` blocks to data.json, so a
        # failure here leaves the standard dashboard fully intact. Not fatal
        # for that reason -- publish the core numbers even if this step trips.
        log(tee)
        log(tee, "--- building performance scorecard ---")
        sc_status = run(tee, [python, "build_scorecard.py"], SCRAPER_DIR, env)
        if sc_status != 0:
            log(tee, f"WARNING: scorecard build failed (exit {sc_status}); "
                     f"analytics.html will show the previous window.")

        if want_push:
            do_push(tee)

        return 0
    finally:
        tee.close()
        # Convenience copy so you can always open the most recent run.
        # A copy rather than a symlink: Windows needs admin rights for those.
        try:
            shutil.copyfile(log_file, LOG_DIR / "latest.log")
        except OSError:
            pass
        prune_logs()


if __name__ == "__main__":
    sys.exit(main())
