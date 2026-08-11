#!/bin/bash
#
# Schedules the daily CCMS refresh on this Mac: scrape -> rebuild ->
# commit -> push. GitHub Pages then republishes automatically.
#
#   ./install_schedule.sh          install (runs pre-flight checks first)
#   ./install_schedule.sh --remove uninstall
#   ./install_schedule.sh --status show whether it is installed and when it last ran
#
# Why not GitHub Actions: GitHub's runners are outside India and the CCMS
# site may refuse them. Running here means the request comes from your own
# connection. The Actions workflow is still in the repo if you prefer it --
# see README.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="gov.kfd.ccms-scrape"
PLIST_SRC="$PROJECT_DIR/$LABEL.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-}" in
  --remove)
    launchctl unload "$PLIST_DEST" 2>/dev/null
    rm -f "$PLIST_DEST"
    echo "Removed $LABEL. The daily refresh will no longer run."
    exit 0 ;;
  --status)
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
      echo "Installed and loaded:"
      launchctl list | grep "$LABEL" | awk '{print "  exit code of last run: "$2}'
    else
      echo "Not installed. Run ./install_schedule.sh to set it up."
    fi
    [ -f "$PROJECT_DIR/logs/latest.log" ] && {
      echo; echo "Last run log ($(stat -f '%Sm' "$PROJECT_DIR/logs/latest.log" 2>/dev/null)):"
      tail -6 "$PROJECT_DIR/logs/latest.log"; }
    exit 0 ;;
esac

echo "=== Pre-flight checks ==="
fail=0

check () {  # check <label> <command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  OK    $label"
  else
    echo "  FAIL  $label"
    fail=1
  fi
}

check "python3 available"            command -v python3
check "playwright installed"         python3 -c "import playwright"
check "git available"                command -v git
check "this is a git repository"     test -d "$PROJECT_DIR/.git"
check "a git remote is configured"   git -C "$PROJECT_DIR" remote get-url origin
check "update.sh is executable"      test -x "$PROJECT_DIR/update.sh"

# Can we push without a human typing a password? This is the one that
# catches people out -- a scheduled job cannot answer a prompt.
if git -C "$PROJECT_DIR" ls-remote --exit-code origin >/dev/null 2>&1; then
  echo "  OK    git can reach the remote without prompting"
else
  echo "  FAIL  git cannot reach the remote without prompting"
  echo "        A scheduled job cannot type a password. Fix with either:"
  echo "          gh auth login                    (easiest)"
  echo "          git remote set-url origin git@github.com:USER/REPO.git   (SSH)"
  fail=1
fi

if [ $fail -ne 0 ]; then
  echo
  echo "Fix the FAIL items above, then run this again. Nothing was installed."
  exit 1
fi

echo
echo "=== Installing ==="
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"
chmod +x "$PROJECT_DIR/update.sh"

sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DEST"
launchctl unload "$PLIST_DEST" 2>/dev/null
launchctl load "$PLIST_DEST" || { echo "launchctl load failed"; exit 1; }

echo "  installed $LABEL"
echo "  project:  $PROJECT_DIR"
echo "  schedule: every day at 07:30"
echo
echo "=== Next ==="
echo "  Run it once now to confirm end to end:"
echo "      launchctl start $LABEL"
echo "      tail -f $PROJECT_DIR/logs/latest.log"
echo
echo "  Check status later:   ./install_schedule.sh --status"
echo "  Change the time:      edit StartCalendarInterval in $LABEL.plist, re-run this"
echo "  Uninstall:            ./install_schedule.sh --remove"
echo
echo "  Note: launchd only fires while the Mac is awake and logged in."
