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
#   ./update.sh --no-notify  do not send the Telegram message
#
#   ./update.sh --test-notify[=WHICH]
#       Send a sample Telegram message and stop, without touching the
#       scraper, git or the data. WHICH is one of: success (default),
#       scrape-fail, push-fail, no-change, all. Use this to prove the bot
#       is wired up, and to see what each outcome actually looks like on
#       your phone.
#
# If the scrape fails, nothing is committed or pushed and the previously
# published data stays live -- a broken run can never blank the dashboard.
#
# Every run sends one Telegram message when it ends, whichever way it
# ended. Credentials and setup: see notify_telegram.sh.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

PUSH=1
COMMIT=1
NOTIFY=1
TEST_NOTIFY=""
for a in "$@"; do
  case "$a" in
    --no-push)        PUSH=0 ;;
    --dry-run)        PUSH=0; COMMIT=0 ;;
    --no-notify)      NOTIFY=0 ;;
    --test-notify)    TEST_NOTIFY="success" ;;
    --test-notify=*)  TEST_NOTIFY="${a#*=}" ;;
  esac
done

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HEADLESS="${HEADLESS:-true}"

# --- Telegram reporting -------------------------------------------------
# The message is assembled as the run goes: TG_STAGE says how far it got,
# TG_DETAIL carries whatever is worth telling you about that point. A trap
# on EXIT does the sending, so every way out of this script -- including
# the early `exit`s below and a Ctrl-C -- reports exactly once.

if [ -r "$PROJECT_DIR/notify_telegram.sh" ]; then
  # shellcheck source=notify_telegram.sh
  . "$PROJECT_DIR/notify_telegram.sh"
else
  echo "WARNING: notify_telegram.sh not found; no notifications will be sent."
  NOTIFY=0
  tg_escape() { printf '%s' "$1"; }
fi

TG_STAGE="startup"
TG_DETAIL=""
START_TS="$(date +%s)"
HOST_NAME="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo pi)"
SCRAPE_OUT="$(mktemp "${TMPDIR:-/tmp}/ccms_scrape.XXXXXX")"

tg_report() {
  local code="$1"

  if [ "$NOTIFY" -ne 1 ]; then
    rm -f "$SCRAPE_OUT"
    return 0
  fi

  local elapsed=$(( $(date +%s) - START_TS ))
  local when body head
  when="$(date '+%a %d %b, %H:%M')"

  # A failed push is not a failed scrape: the data is good and sitting in
  # a local commit, which is a different problem from the one you fix at
  # 07:40. Say so in the first line, since that is all a phone shows.
  if [ "$code" -eq 0 ]; then
    head="✅ <b>CCMS scrape OK</b>"
  elif [ "$TG_STAGE" = "push" ]; then
    head="⚠️ <b>CCMS scraped OK, publish FAILED</b>"
  else
    head="❌ <b>CCMS scrape FAILED</b> (exit $code, at: $TG_STAGE)"
  fi

  body="$head
<i>${when} · $((elapsed / 60))m$((elapsed % 60))s · ${HOST_NAME}</i>"

  if [ -n "$TG_DETAIL" ]; then
    body="$body

$TG_DETAIL"
  fi

  # The scraper's own output is the only thing that explains a scrape
  # failure, so carry the tail of it. Other failures (a failed push,
  # mainly) say all they need to in TG_DETAIL, and a scraper tail there
  # would only point you at the wrong thing.
  if [ "$code" -ne 0 ] && [ "$TG_STAGE" = "scrape" ] && [ -s "$SCRAPE_OUT" ]; then
    local tail_txt
    tail_txt="$(tail -n 15 "$SCRAPE_OUT")"
    body="$body

<pre>$(tg_escape "$tail_txt")</pre>"
  fi

  tg_send "$body" || true
  rm -f "$SCRAPE_OUT"
}
trap 'tg_report $?' EXIT

# --- --test-notify ------------------------------------------------------
# Send sample messages through the real tg_report above and stop. The
# scraper, git and the data are never touched, so this is safe to run at
# any time, including on a machine that has no CCMS access at all. What
# arrives on your phone is byte-for-byte what a real run would send --
# that is the point of driving the real function rather than writing a
# second, prettier copy of the message here.

if [ -n "$TEST_NOTIFY" ]; then
  SAMPLE_SUMMARY="data as of 2026-08-12  |  total pending: 2,777
comparing against 2026-08-11 for the up/down arrows"

  tg_sample() {
    case "$1" in
      success)
        TG_STAGE="push"
        TG_DETAIL="$(tg_escape "$SAMPLE_SUMMARY")

Committed and pushed. GitHub Pages republishes in about a minute."
        START_TS=$(( $(date +%s) - 254 ))
        tg_report 0
        ;;
      no-change)
        TG_STAGE="commit"
        TG_DETAIL="$(tg_escape "$SAMPLE_SUMMARY")

No change since the last run -- nothing to commit."
        START_TS=$(( $(date +%s) - 231 ))
        tg_report 0
        ;;
      scrape-fail)
        TG_STAGE="scrape"
        TG_DETAIL="Nothing committed or pushed. The published dashboard still shows the last good day."
        SCRAPE_OUT="$(mktemp "${TMPDIR:-/tmp}/ccms_scrape.XXXXXX")"
        cat >"$SCRAPE_OUT" <<'SAMPLE'
  [4/8] Civil > Pending  ... 412 rows
  [5/8] Criminal > Pending  ... 0 rows
  [6/8] Criminal > Disposed
Traceback (most recent call last):
  File "scrape_ccms.py", line 288, in <module>
    main()
  File "scrape_ccms.py", line 231, in main
    page.wait_for_selector("#reportTable", timeout=60000)
playwright._impl._errors.TimeoutError: Timeout 60000ms exceeded.
SAMPLE
        START_TS=$(( $(date +%s) - 96 ))
        tg_report 1
        ;;
      push-fail)
        TG_STAGE="push"
        TG_DETAIL="$(tg_escape "$SAMPLE_SUMMARY")

The scrape worked and the commit is saved locally, but the push failed -- the site keeps showing the old data until it goes up.

<pre>$(tg_escape "git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.")</pre>"
        START_TS=$(( $(date +%s) - 268 ))
        tg_report 1
        ;;
      *)
        echo "Unknown --test-notify value: $1" >&2
        echo "Use one of: success, no-change, scrape-fail, push-fail, all" >&2
        NOTIFY=0
        exit 2
        ;;
    esac
  }

  if [ "$TEST_NOTIFY" = "all" ]; then
    for which in success no-change scrape-fail push-fail; do
      echo "sending sample: $which"
      tg_sample "$which"
      sleep 1          # keep them in order and off Telegram's rate limit
    done
  else
    echo "sending sample: $TEST_NOTIFY"
    tg_sample "$TEST_NOTIFY"
  fi

  echo "done -- check Telegram. Nothing was scraped, committed or pushed."
  NOTIFY=0             # the EXIT trap must not add a message of its own
  exit 0
fi

echo "=== CCMS update  $(date '+%Y-%m-%d %H:%M') ==="
echo

cd scraper || {
  echo "FATAL: no scraper/ directory"
  TG_DETAIL="No <code>scraper/</code> directory in $(tg_escape "$PROJECT_DIR")."
  exit 1
}

TG_STAGE="scrape"
python3 scrape_ccms.py 2>&1 | tee "$SCRAPE_OUT"
STATUS=${PIPESTATUS[0]}
cd "$PROJECT_DIR" || exit 1

if [ $STATUS -ne 0 ]; then
  echo
  echo "SCRAPE FAILED (exit $STATUS). Nothing committed, nothing pushed."
  echo "The currently published dashboard is untouched."
  TG_DETAIL="Nothing committed or pushed. The published dashboard still shows the last good day."
  exit $STATUS
fi

# Show what actually changed, so a silent no-op is visible.
echo
SUMMARY=""
if [ -f public/data.json ]; then
  SUMMARY="$(python3 - <<'PY'
import json
try:
    d = json.load(open("public/data.json"))
    tot = sum((c.get("totals", {}).get("total_cases_pending") or 0)
              for c in d.get("case_types", []))
    print(f"data as of {d.get('latest_date')}  |  total pending: {tot:,}")
    if d.get("previous_date"):
        print(f"comparing against {d['previous_date']} for the up/down arrows")
    else:
        print("no earlier snapshot yet -- arrows appear from the next run onwards")
except Exception as e:
    print("could not summarise public/data.json:", e)
PY
)"
  echo "$SUMMARY"
fi
[ -n "$SUMMARY" ] && TG_DETAIL="$(tg_escape "$SUMMARY")"

TG_STAGE="commit"

if [ $COMMIT -eq 0 ]; then
  echo
  echo "--dry-run: stopping before commit."
  TG_DETAIL="$TG_DETAIL

<i>--dry-run: nothing committed.</i>"
  exit 0
fi

if [ ! -d .git ]; then
  echo
  echo "Not a git repository yet. To set one up:"
  echo "  git init && git add . && git commit -m 'CCMS dashboard'"
  echo "  git branch -M main"
  echo "  git remote add origin https://github.com/<you>/<repo>.git"
  echo "  git push -u origin main"
  TG_DETAIL="$TG_DETAIL

⚠️ Not a git repository -- data saved locally only, nothing published."
  exit 0
fi

echo
git add public/data.json public/data.js data/snapshots data/report_headers.json 2>/dev/null
if git diff --staged --quiet 2>/dev/null; then
  echo "No data changes since the last run -- nothing to commit."
  TG_DETAIL="$TG_DETAIL

No change since the last run -- nothing to commit."
  exit 0
fi

git -c user.name="ccms-bot" -c user.email="ccms-bot@local" \
    commit -q -m "CCMS data $(date '+%Y-%m-%d')" && echo "committed."

if [ $PUSH -eq 1 ]; then
  TG_STAGE="push"
  PUSH_OUT="$(git push 2>&1)"
  PUSH_STATUS=$?
  echo "$PUSH_OUT"
  if [ $PUSH_STATUS -eq 0 ]; then
    echo "pushed. GitHub Pages will republish in about a minute."
    TG_DETAIL="$TG_DETAIL

Committed and pushed. GitHub Pages republishes in about a minute."
  else
    echo
    echo "PUSH FAILED. The commit is saved locally -- run 'git push' once"
    echo "your credentials are sorted (an SSH remote is easiest for"
    echo "unattended runs)."
    TG_DETAIL="$TG_DETAIL

The scrape worked and the commit is saved locally, but the push failed -- the site keeps showing the old data until it goes up.

<pre>$(tg_escape "$(printf '%s' "$PUSH_OUT" | tail -n 8)")</pre>"
    exit 1
  fi
else
  echo "--no-push: commit made, push it yourself with 'git push'."
  TG_DETAIL="$TG_DETAIL

Committed locally. <i>--no-push</i>: push it yourself to publish."
fi
