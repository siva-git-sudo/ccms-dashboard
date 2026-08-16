#!/bin/bash
#
# Telegram notifications for the CCMS scrape.
#
# Two ways to use it:
#
#   source notify_telegram.sh          then call tg_send "<message>"
#   ./notify_telegram.sh "hello"       send one message (handy for testing)
#
# Credentials are read, in this order, from the first place that has them:
#
#   1. the environment, if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set
#   2. ~/.config/ccms/telegram.env
#   3. <project>/.telegram.env
#
# Either file is a two-line shell fragment:
#
#   TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   TELEGRAM_CHAT_ID=987654321
#
# Keep it out of git and off other people's eyes:  chmod 600
#
# Nothing in here is allowed to fail the scrape. A missing token, a dead
# network, a Telegram outage -- all of it warns on stderr and returns
# non-zero, and the caller ignores that. A notifier that can take the job
# down with it is worse than no notifier.

TG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Messages are sent with parse_mode=HTML, so only three characters need
# escaping. Run every piece of dynamic text (log tails, git errors) through
# this -- an unescaped '<' in an error message makes Telegram reject the
# whole call with a 400.
#
# sed, not bash's ${s//&/&amp;}: from bash 5.2 an unescaped '&' in a
# ${//} replacement means "the text that matched", so the pure-bash
# version turns '<' into '<lt;' on a current Pi and into '&lt;' on an
# older one. sed's '\&' is unambiguous everywhere.
tg_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

tg_load_config() {
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    return 0
  fi
  local f
  for f in "$HOME/.config/ccms/telegram.env" "$TG_DIR/.telegram.env"; do
    if [ -r "$f" ]; then
      # shellcheck disable=SC1090
      . "$f"
      if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        return 0
      fi
    fi
  done
  return 1
}

tg_configured() { tg_load_config; }

# tg_send "<html message>"
tg_send() {
  local text="$1"

  if ! tg_load_config; then
    echo "telegram: no credentials found; skipping notification." >&2
    echo "telegram: create ~/.config/ccms/telegram.env with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID." >&2
    return 1
  fi

  # Telegram's hard limit is 4096 characters. Cut well short of it so a
  # long log tail cannot cost you the whole message.
  if [ "${#text}" -gt 3500 ]; then
    text="${text:0:3500}"$'\n'"[truncated]"
  fi

  local url="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
  local body

  if command -v curl >/dev/null 2>&1; then
    body="$(curl -sS --max-time 20 -X POST "$url" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=${text}" \
      --data-urlencode "parse_mode=HTML" \
      --data-urlencode "disable_web_page_preview=true" 2>&1)"
  else
    # No curl on a Lite image is unusual but possible; python3 is
    # guaranteed here because the scraper needs it.
    body="$(TG_URL="$url" TG_CHAT="$TELEGRAM_CHAT_ID" TG_TEXT="$text" python3 - <<'PY' 2>&1
import os, urllib.parse, urllib.request
data = urllib.parse.urlencode({
    "chat_id": os.environ["TG_CHAT"],
    "text": os.environ["TG_TEXT"],
    "parse_mode": "HTML",
    "disable_web_page_preview": "true",
}).encode()
try:
    with urllib.request.urlopen(os.environ["TG_URL"], data=data, timeout=20) as r:
        print(r.read().decode("utf-8", "replace"))
except Exception as e:
    print("error:", e)
PY
)"
  fi

  case "$body" in
    *'"ok":true'*) return 0 ;;
    *)
      # Never print the token, even in an error path -- these logs get
      # pasted into chats and issue trackers.
      echo "telegram: send failed: ${body//${TELEGRAM_BOT_TOKEN}/<token>}" >&2
      return 1
      ;;
  esac
}

# Called directly rather than sourced: send the argument and exit.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  set -uo pipefail
  msg="${1:-<b>CCMS test</b>
If you can read this, the bot is wired up correctly.}"
  if tg_send "$msg"; then
    echo "sent."
  else
    echo "not sent -- see the message above." >&2
    exit 1
  fi
fi
