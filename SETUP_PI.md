# Running the daily CCMS refresh on a Raspberry Pi 3

The Mac setup (`install_schedule.sh` + `gov.kfd.ccms-scrape.plist`) only fires
while the Mac is awake and logged in. A Pi sitting in the office is a better
host for a 07:30 job: it is always on, and — the original reason for not using
GitHub Actions — the request still comes from your own connection inside India,
which GitHub's runners cannot do.

Everything below is done once, on the Pi. The end state is a systemd timer that
runs `update.sh` every morning: scrape → rebuild → commit → push, with GitHub
Pages republishing on its own.

Two things differ from the Mac and neither is optional:

- **The OS must be 64-bit.** Playwright publishes no wheels for 32-bit ARM
  (`armv7l`), which is what Raspberry Pi OS installs by default on a Pi 3.
- **Playwright cannot download its own Chromium on ARM.** `playwright install
  chromium` has no ARM build to fetch. You use the Pi's own Chromium instead
  (step 5).

---

## 0. Before you start: is a Pi 3 enough?

A Pi 3B/3B+ has **1 GB of RAM** and a Cortex-A53. Chromium headless plus eight
sequential report downloads fits, but only with swap configured (step 2) and a
larger `/dev/shm` (step 5). Expect the run to take substantially longer than on
the Mac — time your first run and size the systemd timeout off that (step 8).

If a Pi 4 or 5 is available, use it instead and skip nothing else; every step
below is identical. The Pi 3 is workable, not comfortable.

You will also want the Pi on wired ethernet and a **good power supply and SD
card**. A daily write cycle on a cheap card is the most common way these boxes
die silently.

---

## 1. Flash 64-bit Raspberry Pi OS Lite

Use Raspberry Pi Imager and pick **Raspberry Pi OS Lite (64-bit)** — not the
32-bit image, and no desktop needed. In the Imager's settings gear, set the
hostname, enable SSH, create your user, and set the locale to Asia/Kolkata so
the 07:30 in the timer means 07:30 IST.

Boot it, SSH in, and confirm both facts before going further:

```bash
uname -m          # must print: aarch64      (if armv7l, reflash with the 64-bit image)
timedatectl       # Time zone must be Asia/Kolkata
```

If the timezone is wrong: `sudo timedatectl set-timezone Asia/Kolkata`.

Then update:

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

---

## 2. Give it swap

1 GB of RAM is not enough headroom for Chromium. Raise the default 100 MB swap
file to 1 GB:

```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
free -h            # Swap total should now read ~1.0Gi
```

Swap on an SD card is slow and adds write wear. It is here as a safety net so a
memory spike fails slowly instead of the kernel killing Chromium mid-scrape —
not as something the job should live in.

---

## 3. Install the system packages

```bash
sudo apt install -y git python3-pip python3-venv chromium fonts-liberation
```

On some images the package is named `chromium-browser` instead of `chromium`.
Check which you got — you need the path in step 5:

```bash
command -v chromium || command -v chromium-browser
```

---

## 4. Clone the repo and install Playwright

```bash
git clone <your repo url> ~/ccms-dashboard
cd ~/ccms-dashboard
```

**Install Playwright into the system Python, not a venv.** This looks wrong and
is deliberate: `update.sh` forces `PATH="/usr/local/bin:...:/usr/bin:/bin:..."`
before calling `python3`, so `/usr/bin/python3` wins over anything a venv put
earlier on the path. A venv would be silently ignored and the job would fail
with `ModuleNotFoundError: playwright`. On a single-purpose Pi, system-wide is
the honest answer:

```bash
sudo pip3 install --break-system-packages 'playwright>=1.45'
python3 -c "import playwright; print(playwright.__version__)"
```

*(If you would rather keep a venv, the alternative is a one-line edit to
`update.sh` — prepend the venv's `bin` to that `export PATH=` line. Then it is
a local modification you have to remember on every `git pull`.)*

Do **not** run `playwright install chromium`. It will fail, or worse, appear to
succeed and leave a broken directory. Step 5 replaces it.

---

## 5. Point Playwright at the Pi's Chromium

`scrape_ccms.py` calls `p.chromium.launch(...)` with no `executable_path`, so
Playwright looks for its own build in `~/.cache/ms-playwright/`. Put the system
Chromium where it expects to find one. Ask Playwright which revision it wants:

```bash
python3 -m playwright install --dry-run chromium
```

It prints an install location like
`/home/pi/.cache/ms-playwright/chromium-1129`. Use that exact directory:

```bash
REV=/home/$USER/.cache/ms-playwright/chromium-1129        # <-- from the line above
CHROME=$(command -v chromium || command -v chromium-browser)

mkdir -p "$REV/chrome-linux"
ln -sf "$CHROME" "$REV/chrome-linux/chrome"
touch "$REV/INSTALLATION_COMPLETE"
```

Also enlarge `/dev/shm`. Chromium uses it heavily and the default on a 1 GB Pi
is 512 MB shared with everything else:

```bash
echo 'tmpfs /dev/shm tmpfs defaults,size=512M 0 0' | sudo tee -a /etc/fstab
sudo mount -o remount /dev/shm
```

Now test the browser end to end before involving the scraper:

```bash
python3 - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(); pg.goto("https://example.com")
    print("OK:", pg.title())
    b.close()
PY
```

If this prints `OK: Example Domain`, the hard part is done.

> **The one caveat with this shim:** Playwright is tested against the Chromium
> build it ships, and Debian's is a different version. Basic navigation, form
> fills and downloads — all this scraper does — work in practice. If a future
> `apt upgrade` of Chromium breaks the run, the log will show a protocol error
> at launch, and the fix is to pin the Chromium package or match the Playwright
> version to it.

---

## 6. Give git a way to push without a password

A scheduled job cannot answer a prompt. Use an SSH deploy key:

```bash
ssh-keygen -t ed25519 -C "ccms-pi" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Add that public key to the GitHub repo under **Settings → Deploy keys**, with
**Allow write access** ticked. Then switch the remote to SSH and prove it works
non-interactively:

```bash
cd ~/ccms-dashboard
git remote set-url origin git@github.com:<you>/<repo>.git
ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # accepts the host key once
git ls-remote origin >/dev/null && echo "git OK, no prompt"
```

That last line is the same check `install_schedule.sh` runs on the Mac, and it
is the step people most often skip.

---

## 7. Run it once by hand

```bash
cd ~/ccms-dashboard
chmod +x update.sh
time ./update.sh --dry-run          # scrapes, rebuilds, commits nothing
```

`--dry-run` means a bad first run cannot push anything. Watch it, note the
elapsed time from `time`, and check the summary line it prints
(`data as of ... | total pending: ...`).

When that looks right, do a real one:

```bash
./update.sh
```

Confirm the commit landed on GitHub and the published dashboard moved.

---

## 8. Schedule it with a systemd timer

systemd, not cron: `Persistent=true` makes it catch up after a power cut
instead of skipping the day, which is the behaviour the launchd job had.

Create the service — substitute your username for `pi` in all four places:

```bash
sudo tee /etc/systemd/system/ccms-scrape.service >/dev/null <<'EOF'
[Unit]
Description=CCMS daily scrape, rebuild and publish
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/ccms-dashboard
ExecStart=/bin/bash /home/pi/ccms-dashboard/update.sh
Environment=HEADLESS=true
# Generous: a Pi 3 is slow, and a run killed halfway is worse than a late one.
# Set this to roughly 3x the time step 7 took.
TimeoutStartSec=3600
Nice=5

[Install]
WantedBy=multi-user.target
EOF
```

And the timer:

```bash
sudo tee /etc/systemd/system/ccms-scrape.timer >/dev/null <<'EOF'
[Unit]
Description=Run the CCMS scrape every morning at 07:30 IST

[Timer]
OnCalendar=*-*-* 07:30:00
# Spread the load off the exact minute; CCMS is not the only thing waking up.
RandomizedDelaySec=300
# If the Pi was off at 07:30, run once it boots rather than losing the day.
Persistent=true
Unit=ccms-scrape.service

[Install]
WantedBy=timers.target
EOF
```

Enable and check:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ccms-scrape.timer
systemctl list-timers ccms-scrape.timer      # shows NEXT and LEFT
```

---

## 9. Telegram notifications

`update.sh` sends one Telegram message at the end of every run, whichever way
it ended. This is the answer to the failure mode described in step 10: a
scraper that quietly stops is invisible, because the dashboard keeps serving
yesterday's data and looks perfectly healthy.

Put the bot token and your chat ID somewhere the Pi can read and git cannot
see:

```bash
mkdir -p ~/.config/ccms
cat > ~/.config/ccms/telegram.env <<'EOF'
TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
EOF
chmod 600 ~/.config/ccms/telegram.env
```

`<project>/.telegram.env` works too and is gitignored — use that one if the
systemd unit ever runs with a `$HOME` you did not expect.

Prove it works without touching CCMS at all:

```bash
cd ~/ccms-dashboard
./update.sh --test-notify           # one sample success message
./update.sh --test-notify=all       # all four outcomes, so you know each on sight
```

`--test-notify` drives the same reporting code a real run does, so what lands
on your phone is exactly what a real run would send. It never starts the
scraper and never commits or pushes — safe to run at any time, including
before the Pi has CCMS access working.

The four messages you can get:

| First line | What happened |
|---|---|
| ✅ CCMS scrape OK | Scraped, then either published or found nothing new to publish. Totals are in the message. |
| ⚠️ CCMS scraped OK, publish FAILED | Data is good and committed locally, but `git push` failed — the site still shows the old day. Usually the deploy key (step 6). |
| ❌ CCMS scrape FAILED | The scraper exited non-zero. Nothing committed. The last 15 lines of its output come with the message. |
| ❌ CCMS scrape FAILED (at: startup) | It could not even begin — wrong directory, most likely. |

Two things worth knowing:

- **The notifier can never take the scrape down with it.** A missing token, no
  network, a Telegram outage — each warns on stderr and the run carries on and
  finishes normally. That is deliberate: a monitor that can break the thing it
  monitors is worse than no monitor.
- **`--no-notify`** suppresses the message for a one-off manual run.

If nothing arrives at all, look for a line starting `telegram:` in
`journalctl -u ccms-scrape.service -n 50` — a missing credentials file and a
rejected token both announce themselves there.

---

## 10. Operating it

```bash
sudo systemctl start ccms-scrape.service     # run now, out of schedule
tail -f ~/ccms-dashboard/logs/latest.log     # the scraper's own log
journalctl -u ccms-scrape.service -n 50      # what systemd saw
systemctl list-timers ccms-scrape.timer      # when it next fires
sudo systemctl disable --now ccms-scrape.timer   # stop the daily run
```

`update.sh` keeps the last 30 logs itself, so nothing extra is needed for
rotation.

**Change the time:** edit `OnCalendar` in the timer, then
`sudo systemctl daemon-reload && sudo systemctl restart ccms-scrape.timer`.

**Know when it breaks.** A silent scraper is the failure mode that matters —
the dashboard keeps showing yesterday's data and looks fine. `update.sh`
refuses to commit on a failed scrape, so the published site is never blanked,
but on its own nothing tells you. Step 9 is what tells you: a Telegram message
on every run, success or failure. Set it up and the daily message becomes the
heartbeat — a morning with no message at all means the Pi itself is down, which
no failure alert could have told you.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `pip install playwright` fails, no matching distribution | 32-bit OS. `uname -m` must be `aarch64`; reflash with the 64-bit image. |
| `Executable doesn't exist at ~/.cache/ms-playwright/...` | Step 5's symlink path does not match the revision Playwright wants. Re-run `playwright install --dry-run chromium` and redo it. |
| `ModuleNotFoundError: playwright` under systemd, but fine by hand | Playwright is in a venv. `update.sh` forces `/usr/bin` first — see step 4. |
| Chromium dies mid-run, no clear error | Out of memory. Confirm swap (step 2) and `/dev/shm` (step 5); check `dmesg | grep -i oom`. |
| Push fails, commit stays local | Deploy key missing write access, or the remote is still HTTPS. Re-run the step 6 checks. |
| Timer never fires | Timer enabled but not started, or wrong timezone. `systemctl list-timers` and `timedatectl`. |
| Protocol / target-closed error at launch, after an `apt upgrade` | Debian Chromium moved too far from the Playwright version. See the caveat in step 5. |
| No Telegram message, run otherwise fine | Credentials file missing or unreadable by the service user. `./update.sh --test-notify` prints the reason. |
| Telegram says `Unauthorized` or `chat not found` | Wrong token, or you have never sent the bot a message — a bot cannot open a conversation with you first. |

---

## Sources

- [Playwright BrowserType API — `executable_path`](https://playwright.dev/python/docs/api/class-browsertype)
- [playwright-python #2577 — no arm32 support](https://github.com/microsoft/playwright-python/issues/2577)
- [playwright-python #976 — ARM64 driver](https://github.com/microsoft/playwright-python/issues/976)
- [free-games-claimer #3 — Raspberry Pi requires a 64-bit OS](https://github.com/vogler/free-games-claimer/issues/3)
- [11chri/playwright-rpi — Playwright on Raspberry Pi](https://github.com/11chri/playwright-rpi)
