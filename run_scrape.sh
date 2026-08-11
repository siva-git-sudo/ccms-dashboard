#!/bin/bash
#
# Background runner for the CCMS scrape.
#
# Runs the scraper headless (no visible browser), logs everything to
# logs/, and optionally deploys to Firebase if the scrape succeeded.
# Designed to be called by launchd/cron with no terminal attached, so it
# uses absolute paths throughout and never prompts for anything.
#
#   ./run_scrape.sh            scrape only
#   ./run_scrape.sh --deploy   scrape, then firebase deploy if it worked
#   ./run_scrape.sh --push     scrape, then commit + push to git
#                              (GitHub Pages then republishes on its own)
#   ./run_scrape.sh --push --deploy   both
#
# Exit codes: 0 = scrape succeeded, 1 = scrape failed (nothing published).

set -uo pipefail

# Resolve this script's directory so the job works regardless of cwd.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRAPER_DIR="$PROJECT_DIR/scraper"
LOG_DIR="$PROJECT_DIR/logs"

# Parse flags (order-independent).
WANT_DEPLOY=0
WANT_PUSH=0
for arg in "$@"; do
  case "$arg" in
    --deploy) WANT_DEPLOY=1 ;;
    --push)   WANT_PUSH=1 ;;
  esac
done

mkdir -p "$LOG_DIR"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG_FILE="$LOG_DIR/scrape_$TIMESTAMP.log"
LATEST_LINK="$LOG_DIR/latest.log"

# launchd/cron run with a minimal PATH -- make sure the usual install
# locations for python3, node and firebase are reachable.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Headless is the default in scrape_ccms.py; set it explicitly so this is
# obvious to anyone reading the job definition.
export HEADLESS=true

{
  echo "=== CCMS scrape started $(date) ==="
  echo "project: $PROJECT_DIR"
  echo "python:  $(command -v python3 || echo 'NOT FOUND')"
  echo

  cd "$SCRAPER_DIR" || { echo "FATAL: cannot cd to $SCRAPER_DIR"; exit 1; }

  python3 scrape_ccms.py
  SCRAPE_STATUS=$?

  echo
  if [ $SCRAPE_STATUS -ne 0 ]; then
    echo "=== SCRAPE FAILED (exit $SCRAPE_STATUS) at $(date) ==="
    echo "No snapshot written, no deploy attempted. Previous data left intact."
    exit $SCRAPE_STATUS
  fi

  echo "=== scrape OK at $(date) ==="

  if [ "$WANT_PUSH" = "1" ]; then
    echo
    echo "--- committing and pushing to git ---"
    cd "$PROJECT_DIR" || exit 1
    if [ ! -d .git ]; then
      echo "WARNING: $PROJECT_DIR is not a git repository; skipping push."
      echo "Set one up with:  git init && git remote add origin <url>"
    else
      git add data/snapshots public/data.json public/data.js 2>/dev/null
      if git diff --staged --quiet 2>/dev/null; then
        echo "No data changes to commit."
      else
        git -c user.name="ccms-bot" -c user.email="ccms-bot@local" \
            commit -m "CCMS snapshot $(date +%Y-%m-%d)" && echo "committed."
        if git push 2>&1; then
          echo "pushed."
        else
          echo "WARNING: git push failed (check credentials / remote)."
        fi
      fi
    fi
  fi

  if [ "$WANT_DEPLOY" = "1" ]; then
    echo
    echo "--- deploying to Firebase ---"
    cd "$PROJECT_DIR" || exit 1
    if ! command -v firebase >/dev/null 2>&1; then
      echo "WARNING: firebase CLI not found on PATH; skipping deploy."
      echo "Install with: npm install -g firebase-tools"
      exit 0
    fi
    firebase deploy --only hosting --non-interactive
    DEPLOY_STATUS=$?
    if [ $DEPLOY_STATUS -ne 0 ]; then
      echo "WARNING: deploy failed (exit $DEPLOY_STATUS), but the scrape data is saved."
    else
      echo "=== deploy OK at $(date) ==="
    fi
  fi

  exit 0
} 2>&1 | tee "$LOG_FILE"

STATUS=${PIPESTATUS[0]}

# Convenience symlink so `tail -f logs/latest.log` always follows the
# most recent run.
ln -sf "$LOG_FILE" "$LATEST_LINK"

# Keep the last 30 logs, drop older ones.
ls -1t "$LOG_DIR"/scrape_*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null

exit "$STATUS"
