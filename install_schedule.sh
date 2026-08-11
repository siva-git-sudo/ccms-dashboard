#!/bin/bash
#
# Installs the CCMS scrape as a macOS background job (launchd), so it runs
# automatically every weekday morning with no terminal and no visible
# browser.
#
#   ./install_schedule.sh          install / reinstall
#   ./install_schedule.sh --remove uninstall
#
# After installing, check it with:
#   launchctl list | grep ccms
#   tail -f logs/latest.log

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="gov.kfd.ccms-scrape"
PLIST_SRC="$PROJECT_DIR/$LABEL.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
  rm -f "$PLIST_DEST"
  echo "Removed $LABEL."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"
chmod +x "$PROJECT_DIR/run_scrape.sh"

# Substitute the real project path into the plist template.
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DEST"

# Reload (unload first so reinstalling picks up changes).
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Installed $LABEL"
echo "  plist:   $PLIST_DEST"
echo "  project: $PROJECT_DIR"
echo "  logs:    $PROJECT_DIR/logs/"
echo
echo "Scheduled: every day at 07:30 (edit the plist to change)."
echo
echo "Verify with:   launchctl list | grep ccms"
echo "Run now with:  launchctl start $LABEL"
echo "Watch logs:    tail -f $PROJECT_DIR/logs/latest.log"
echo "Uninstall:     ./install_schedule.sh --remove"
