# airtel

Auto-recovery toolkit for a flaky Airtel/Tozed ZLT X25 5G CPE router.
Talks directly to the router's own JSON API (no browser automation) to
check status, force a reconnect, or reboot — and watches the connection
in the background so you don't have to.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ROUTER_PASS
```

## Usage

```bash
python cli.py status       # live signal/connection JSON
python cli.py reconnect    # fast WAN/cellular toggle (~10s)
python cli.py reboot       # full reboot (~60-90s)

python monitor.py          # run the autonomous health-check daemon
```

`monitor.py` pings `PING_TARGET` on a loop; after `FAIL_THRESHOLD`
consecutive misses it reconnects, and escalates to a reboot if the next
outage isn't cleared by a reconnect alone. Tune thresholds in `.env`.

See `docs/mac-whitelist.md` for locking Wi-Fi access to known devices, and
`CLAUDE.md` for what's been learned about this specific device/firmware.
