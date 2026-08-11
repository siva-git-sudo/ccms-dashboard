# CCMS court case dashboard

Tracks pending case counts (CCMS "Outside Secretariat" report, Forest
Ecology & Environment dept) for: Bengaluru Circle, Bengaluru Urban
Division, Bengaluru Rural Division, Chikkaballapura Division, Kolar
Division, Ramanagara Division, Bannerghatta Wildlife Division, and their
Social Forestry counterparts. Each division's number is the sum across
**both courts and every case type**:

- High Court of Karnataka — Civil Contempt Petition, Writ Appeal, Writ Petition
- KSAT (Karnataka State Administrative Tribunal) — Contempt Application,
  Miscellaneous Application, Original Application, Review Application

Dashboard shows a green ▲ for an increase and a red ▼ for a decrease vs.
the previous scrape, with the delta number. Each division card expands to
show the same increase/decrease breakdown per user (office/designation
within that division, e.g. "Assistant Administrator", "Conservator of
Forests" -- the report's `PostNm` rows).

## Setup

```bash
cd scraper
pip install -r requirements.txt
playwright install chromium
```

## Run the scraper (do this daily)

```bash
cd scraper
python scrape_ccms.py
```

This opens ccms.karnataka.gov.in/ccms/StateReport.aspx, sets the
department dropdown to **--All--** (every circle/division under Forest,
Ecology & Environment in one report), and pulls 7 reports total — one per
(court, case type) combo: HC Civil Contempt/Writ Appeal/Writ Petition,
KSAT Contempt/Miscellaneous/Original/Review Application. Each report is
filtered down to the 12 divisions in `divisions.json` (matched by name)
and summed into one "all courts, all case types" total per division.
Raw XML exports go to `data/raw/<date>/`, the summed + per-officer
snapshot goes to `data/snapshots/<date>.json`, and `public/data.json` is
regenerated (today vs. the most recent prior snapshot).

**Zero vs. unknown.** A division only appears in a report if it *has*
cases of that type, so "absent from all 8 reports" normally means a
genuine zero — and it's recorded as `0`. But if any report failed to
scrape, absence is ambiguous, so it's recorded as `null` and the
dashboard shows "No data yet" rather than a misleading `0`. The snapshot's
`seen_department_names_by_combo` lists every department name each report
returned, so you can confirm a name matches `divisions.json` if a
division looks wrong.

Run it from a machine with internet access to the CCMS site (no login
needed, the report is public). `HEADLESS=false python scrape_ccms.py` runs
it visibly, useful for the first run or if a selector needs adjusting
(see the note at the top of `scrape_ccms.py` — the dropdown fields and
the rblstatereport-before-ddlsecdeptname ordering are confirmed against
the live site; the "View Report" button and Export menu path are still
best-guess).

## Run it in the background (unattended)

The scraper is headless by default — the visible browser window only
appears if you pass `HEADLESS=false`. To run it automatically with no
terminal open:

```bash
./install_schedule.sh
```

That installs a macOS launchd job (`gov.kfd.ccms-scrape`) that runs every
day at 07:30, scrapes headless, and deploys to Firebase if the scrape
succeeded. Useful commands:

```bash
launchctl list | grep ccms          # is it installed?
launchctl start gov.kfd.ccms-scrape # run it right now, in background
tail -f logs/latest.log             # watch the most recent run
./install_schedule.sh --remove      # uninstall
```

To change the time, edit `StartCalendarInterval` in
`gov.kfd.ccms-scrape.plist` and re-run `./install_schedule.sh`.

You can also invoke the runner directly:

```bash
./run_scrape.sh            # scrape only
./run_scrape.sh --deploy   # scrape, then deploy if it succeeded
```

Every run writes a timestamped log to `logs/`, keeps the last 30, and
points `logs/latest.log` at the newest. If the scrape fails, the runner
exits non-zero, **skips the deploy, and leaves the previous good data
untouched** — a broken run can never publish empty numbers or become the
baseline that tomorrow's arrows are measured against.

Note: launchd only fires while the Mac is awake and logged in. If the
machine is often asleep at 07:30, either adjust the time or run this on
an always-on box.

## Scope: department and divisions

Nothing in the scraper, parser or dashboard is forest-specific. Two
environment variables decide what gets pulled:

```bash
# default — Forest, Ecology and Environment, the 12 divisions in divisions.json
python3 scrape_ccms.py

# every division/circle under Forest (100 departments), no list to maintain
CCMS_TRACK_ALL=1 python3 scrape_ccms.py

# every secretariat department in the state (Agriculture, Revenue, ...)
CCMS_DEPT=0 CCMS_TRACK_ALL=1 python3 scrape_ccms.py
```

`CCMS_DEPT` is the `ddlsecdeptname` code (`FE` = Forest, `0` = --All--).
`CCMS_TRACK_ALL=1` derives the division list from the reports themselves
instead of `divisions.json`, so it needs no upkeep when departments are
added or renamed upstream.

Measured statewide (100 departments, 3,071 pending cases, 608 officer
rows): `data.json` is 0.99 MB raw but **30 KB gzipped** — 97% compression,
because the data is overwhelmingly repeated field names and small
integers. Both Firebase and GitHub Pages gzip automatically, so a first
page load costs about 36 KB including the HTML.

| Audience | Uncached transfer/mo | With 30-min cache | Firebase 10 GB | Pages 100 GB |
|---|---|---|---|---|
| 1,000 users | 1.5 GB | 0.8 GB | fine | fine |
| 5,000 users | 7.6 GB | 3.8 GB | fine | fine |
| 10,000 users | 15.2 GB | 7.6 GB | fine *with* caching | fine |
| 20,000 users | 30.3 GB | 15.2 GB | over — use Pages | fine |

Caching is what makes this work, and `firebase.json` now sets
`max-age=1800, stale-while-revalidate=86400` on the data files. An
earlier version used `no-cache, max-age=0`, which forced a full
re-download on every page view — fine for one user, expensive for
thousands. The Refresh button still bypasses the cache explicitly.

For statewide use GitHub Pages: 100 GB/month versus Firebase's 10 GB, and
it costs nothing either way.

## Host on GitHub and update daily

Two options. Try A first; fall back to B if it fails.

### A. Fully automated on GitHub (no machine of yours involved)

`.github/workflows/scrape-and-publish.yml` runs the scraper on GitHub's
servers every day at 02:00 UTC (07:30 IST), commits the new snapshot, and
publishes `public/` to GitHub Pages.

```bash
cd ccms-dashboard
git init && git add . && git commit -m "CCMS dashboard"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Then in the repo on github.com:

1. **Settings → Pages → Source: GitHub Actions**
2. **Settings → Actions → General → Workflow permissions: Read and write**
3. **Actions tab → "Scrape CCMS and publish dashboard" → Run workflow**
   (don't wait for tomorrow — verify it works now)

Your dashboard then lives at `https://<you>.github.io/<repo>/`.

**The risk:** GitHub's runners are outside India, and some Indian
government sites block foreign IP ranges. The workflow checks
connectivity first and fails with a clear message if it can't reach
ccms.karnataka.gov.in — that's what the manual run in step 3 is for. If
it fails there, use option B.

### B. Scrape on your Mac, publish via GitHub

Your Mac does the scraping (so the request comes from an Indian IP) and
pushes the result; GitHub Pages serves it.

```bash
./run_scrape.sh --push
```

To automate, edit `gov.kfd.ccms-scrape.plist` and change the `--deploy`
argument to `--push`, then run `./install_schedule.sh`. Set Pages to
**Settings → Pages → Source: Deploy from a branch → main → /public**.

This needs the Mac awake at the scheduled time, and git credentials that
work non-interactively (use a credential helper or an SSH remote — the
job cannot answer a password prompt).

### Why snapshots must be committed

The ▲/▼ arrows compare today's snapshot with the previous one, so
`data/snapshots/` has to persist in the repo. Both options above commit
it. `data/raw/` and `logs/` are gitignored — they'd bloat the repo and
are reproducible.

## Deploy to Firebase Hosting

```bash
npm install -g firebase-tools   # if not already installed
firebase login
```

Edit `.firebaserc` and replace `YOUR_FIREBASE_PROJECT_ID` with your actual
Firebase project ID, then:

```bash
firebase deploy --only hosting
```

The `public/` folder is the entire site — `index.html` + `data.json`.
Re-running the scraper and re-deploying is all that's needed to refresh
the dashboard.

## Files

- `scraper/divisions.json` — the 12 circles/divisions and their CCMS
  department codes (from the live `ddldeptname` dropdown).
- `scraper/parse_ccms_xml.py` — parses one CCMS XML export into labeled
  fields. Note: each case type is a **separate SSRS report with its own
  column count** (Writ Petition = 17 numeric columns, Civil Contempt =
  20), so columns are read positionally: the first two are always
  "Cases Received As On Yesterday" and "Total Cases Pending". Verified
  against both real exports.
- `scraper/scrape_ccms.py` — Playwright automation that drives the report
  page (department = --All--, one export per court/case-type combo) and
  filters the result down to our target divisions.
- `scraper/build_dashboard_data.py` — turns the snapshot history into
  `public/data.json` (adds the delta + increase/decrease direction).
- `public/index.html` — the dashboard.
- `data/snapshots/` — one JSON file per day scraped; this is the history
  used to compute increase/decrease.
- `run_scrape.sh` — background runner (headless scrape + logging +
  optional deploy).
- `install_schedule.sh` / `gov.kfd.ccms-scrape.plist` — macOS launchd job
  that runs the above on a schedule.
- `logs/` — timestamped log per run; `logs/latest.log` is the newest.
