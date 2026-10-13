# airtel

A background auto-recovery CLI for flaky Tozed/ZLT-style router web UIs
(e.g. an Airtel-branded ZLT X25). Talks directly to the router's own JSON
API — no browser automation — to check status, force a reconnect, or
reboot, and watches the connection in the background so you don't have to.

Works with **your own** router: nothing here ships with anyone's real IP,
username, or password baked in. `airtel init` asks for yours and verifies
the login before saving it.

## Install

```bash
# recommended: isolated global install, works from any directory
pipx install .

# or, inside a venv
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

Either way you get a plain `airtel` command on your `$PATH`.

## First run

```bash
airtel init
```

Prompts for your router's URL (default `http://192.168.1.1`), admin
username (default `admin`), and password — then logs in once to confirm
the credentials actually work before saving them to
`~/.config/airtel/.env` (mode `600`, never committed, never printed back).

## Usage

```bash
airtel              # start the background health-monitor daemon
airtel stop          # stop it
airtel logs           # tail its log

airtel status         # dashboard: signal gauges, latency + trend graphs, traffic
airtel status --watch # live-refreshing dashboard (Ctrl+C to exit)
airtel status --json  # raw router payload
airtel reconnect      # one-off: fast WAN/cellular toggle (~10s)
airtel reboot          # one-off: full reboot (~60-90s)

airtel foreground       # run the monitor attached, for debugging
```

Typing `airtel` with no arguments is the main entry point: it launches the
health-check daemon detached from your terminal and returns immediately.
It pings a target host (`PING_TARGET` in the config, default `8.8.8.8`) on
a loop; after `FAIL_THRESHOLD` consecutive misses it reconnects, and
escalates to a full reboot if the *next* outage isn't cleared by a
reconnect alone. Tune thresholds by editing `~/.config/airtel/.env`.

## Uninstall / reconfigure

```bash
airtel stop
pipx uninstall airtel-router-cli   # or: pip uninstall airtel-router-cli
rm -rf ~/.config/airtel            # drops saved credentials/logs
```

See `docs/mac-whitelist.md` for locking Wi-Fi access to known devices, and
`CLAUDE.md` for what's been learned about the ZLT X25's API while building
this.
