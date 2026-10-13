# Project context: Airtel ZLT X25 router toolkit

## What this project is for
The user's Airtel-provided 5G CPE router (Tozed **ZLT X25**, firmware
4.13.1, SN `X25A861420062751894`) has flaky firmware — traffic
intermittently freezes and needs a reconnect/reboot to recover. This
project automates detecting and recovering from that, plus documents what
was learned about the device while investigating a possible firmware dump
for Airtel support.

## Device facts established this session
- **Admin web UI**: `http://192.168.1.1`, username `admin`. The API is a
  single JSON endpoint at `/cgi-bin/http.cgi`, keyed by a numeric `cmd`
  (see `router_client.py` docstring for the full reverse-engineered map,
  pulled from the router's own `js/main.js` `RequestCmd` object).
- **Login flow**: fetch a one-time token (`cmd 232`), hash the password as
  `sha256(token + password)`, POST `cmd 100` with that hash and a random
  `sessionId`. Successful login returns a `sessionId` used on every later
  call; privileged POST actions additionally need a short-lived action
  token from `cmd 233`.
- **Account privilege is capped**: the `admin` account logs in at
  `user_level: 3`. Log/config export (`cmd 241`, `EXPORT_LOG`) returns
  `LIMITED_ACCESS` at this level — only a higher-tier (carrier-held)
  account can pull that. This matches other ZLT models (e.g. X28 owners
  report the same hidden "superadmin" tier with no public answer).
- **USB-C port**: confirmed via vendor spec sheets to be a **USB 3.0 data
  port** (not power — power is the separate DC barrel jack), but it never
  enumerated on macOS when connected — most likely because it's a host
  port on the router's side (for USB drives), not a device-mode port, so a
  computer won't see it as a peripheral.
- **Chipset is unconfirmed.** One carrier spec sheet for an X25 Max variant
  lists a MediaTek T750; nothing confirms Spreadtrum/Unisoc or Qualcomm for
  this exact unit. This matters if ever attempting a chip-level flash dump
  (EDL for Qualcomm, `spreadtrum_flash`-style tooling for Unisoc, `mtkclient`
  for MediaTek — wrong tool for the wrong chip does nothing).
- **Live signal (as observed)**: network `Airtel Tanzania`, 4G, RSRP
  ~-107 dBm (weak) — worth flagging to support as a possible root cause
  independent of firmware.
- A true raw eMMC/flash dump was *not* pursued programmatically — it would
  require either a chipset-specific download-mode (needs a confirmed SoC
  + loader blob) or physical probing of a UART header on the bare main
  board (not yet located; the antenna stack was photographed, not the
  bare board underneath it).

## What's implemented here
Packaged as an installable CLI (`pipx install .` / `pip install .`) that
puts an `airtel` command on `$PATH` — see `pyproject.toml` for the
`src/` layout and entry point.

- `src/airtel/router_client.py` — direct JSON-API client (login, live
  status, WAN reconnect, reboot). Faster and more robust than DOM-clicking
  a headless browser, since the full request/response shape is known.
- `src/airtel/cli.py` — the `airtel` entry point: `init` (interactive
  setup wizard, verifies login before saving), bare `airtel` (start the
  monitor detached in the background, pid-tracked), `stop`, `logs`,
  `foreground` (attached, for debugging), plus one-off `status` /
  `reconnect` / `reboot`.
- `src/airtel/monitor.py` — the autonomous ping-based health loop; after
  `FAIL_THRESHOLD` consecutive ping misses it triggers a fast WAN toggle,
  and escalates to a full reboot if the *next* outage isn't cleared by a
  toggle alone.
- `docs/mac-whitelist.md` — manual steps for Wi-Fi MAC allow-listing
  (not scripted — ACL payload shape wasn't confirmed this session; see
  that doc for why and how to extend `router_client.py` later).

## Working conventions for this repo
- Config is per-user, not per-project: `airtel init` writes credentials to
  `~/.config/airtel/.env` (mode 600), loaded via `src/airtel/settings.py`.
  Nothing in the repo defaults to any specific person's router password —
  `ROUTER_URL`/`ROUTER_USER` fall back to the firmware's own generic
  defaults (`192.168.1.1`/`admin`), `ROUTER_PASS` has no default. Never
  hardcode a real password in a committed file; a local `.env` in the repo
  root still works for development (gitignored) but ship-facing config
  lives outside the repo.
- Prefer extending `router_client.py` with new `cmd`-based methods over
  adding browser automation (Playwright/Selenium) — the API is already
  mapped and is an order of magnitude faster/more reliable than simulating
  clicks, as long as the `cmd` id and payload shape are known. Only fall
  back to browser automation for actions whose exact API payload hasn't
  been captured yet.
- If extending the ACL/MAC-filter automation: capture the real request via
  browser dev tools (Network tab, filter XHR, look for `MAC_FILTER` /
  `MAC_DEFAULT_FILTER`) before writing code, same as every other command
  here was derived from the router's own `main.js`, not guessed.
