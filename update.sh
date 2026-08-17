#!/bin/bash
#
# One command to refresh the dashboard:
#   scrape CCMS -> rebuild data.json/data.js -> commit -> push
#
# GitHub Pages then republishes the site automatically (see
# .github/workflows/publish.yml). Takes a couple of minutes.
#
#   ./update.sh              scrape and push
#   ./update.sh --no-push    scrape only, leave the commit to you
#   ./update.sh --dry-run    scrape only, do not commit at all
#
# If the scrape fails, nothing is committed or pushed and the previously
# published data stays live -- a broken run can never blank the dashboard.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

PUSH=1
COMMIT=1
for a in "$@"; do
  case "$a" in
    --no-push) PUSH=0 ;;
    --dry-run) PUSH=0; COMMIT=0 ;;
  esac
done

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HEADLESS="${HEADLESS:-true}"

echo "=== CCMS update  $(date '+%Y-%m-%d %H:%M') ==="
echo

cd scraper || { echo "FATAL: no scraper/ directory"; exit 1; }
python3 scrape_ccms.py
STATUS=$?
cd "$PROJECT_DIR" || exit 1

if [ $STATUS -ne 0 ]; then
  echo
  echo "SCRAPE FAILED (exit $STATUS). Nothing committed, nothing pushed."
  echo "The currently published dashboard is untouched."
  exit $STATUS
fi

# Show what actually changed, so a silent no-op is visible.
echo
if [ -f public/data.json ]; then
  python3 - <<'PY'
import json
try:
    d = json.load(open("public/data.json"))
    tot = sum((c.get("totals", {}).get("total_cases_pending") or 0)
              for c in d.get("case_types", []))
    print(f"data as of {d.get('latest_date')}  |  total pending: {tot}")
    if d.get("previous_date"):
        print(f"comparing against {d['previous_date']} for the up/down arrows")
    else:
        print("no earlier snapshot yet -- arrows appear from the next run onwards")
except Exception as e:
    print("could not summarise public/data.json:", e)
PY
fi

if [ $COMMIT -eq 0 ]; then
  echo
  echo "--dry-run: stopping before commit."
  exit 0
fi

if [ ! -d .git ]; then
  echo
  echo "Not a git repository yet. To set one up:"
  echo "  git init && git add . && git commit -m 'CCMS dashboard'"
  echo "  git branch -M main"
  echo "  git remote add origin https://github.com/<you>/<repo>.git"
  echo "  git push -u origin main"
  exit 0
fi

echo
git add public/data.json public/data.js data/snapshots data/report_headers.json 2>/dev/null
if git diff --staged --quiet 2>/dev/null; then
  echo "No data changes since the last run -- nothing to commit."
  exit 0
fi

git -c user.name="ccms-bot" -c user.email="ccms-bot@local" \
    commit -q -m "CCMS data $(date '+%Y-%m-%d')" && echo "committed."

if [ $PUSH -eq 1 ]; then
  # Pull remote changes first (e.g. code commits from a dev machine) so the
  # push is never rejected for being behind. --rebase keeps history linear.
  # If there's a conflict in the data files, ours (the fresh scrape) wins.
  echo "syncing with remote before push ..."
  if git pull --rebase 2>&1; then
    echo "sync OK."
  else
    # Conflict in data files -- take ours (today's scrape) and continue.
    # NOTE: in `git rebase`, --theirs = the local commits being replayed (our
    # fresh scrape), --ours = the upstream/remote. Opposite of `git merge`.
    echo "conflict during pull -- taking local data files and continuing."
    git checkout --theirs public/data.json public/data.js 2>/dev/null
    git add public/data.json public/data.js 2>/dev/null
    GIT_EDITOR=true git rebase --continue 2>&1 || true
  fi

  if git push 2>&1; then
    echo "pushed. GitHub Pages will republish in about a minute."
  else
    echo
    echo "PUSH FAILED. The commit is saved locally -- run 'git push' once"
    echo "your credentials are sorted (an SSH remote is easiest for"
    echo "unattended runs)."
    exit 1
  fi
else
  echo "--no-push: commit made, push it yourself with 'git push'."
fi
