#!/usr/bin/env python3
"""Autonomous connection-health daemon.

Pings a target host on an interval; after FAIL_THRESHOLD consecutive misses
it calls the router's direct API to toggle the WAN/cellular connection
(fast path), then backs off for RECOVERY_GRACE_SECONDS before resuming
checks. Falls back to a full reboot if the fast reconnect doesn't clear
the outage after one retry cycle.

Normally launched via `airtel` (background) or `airtel foreground`
(attached, for debugging). Stop the background one with `airtel stop`,
or Ctrl+C when running in the foreground.
"""
import logging
import re
import subprocess
import sys
import time
from typing import Optional

from . import history, settings
from .router_client import RouterAPIError, RouterAuthError, RouterClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")


_RTT_RE = re.compile(r"time[=<]([\d.]+)")


def ping_rtt(target: str, timeout_s: int = 3) -> Optional[float]:
    """Round-trip time in ms, or None if the ping failed."""
    try:
        out = subprocess.run(
            ["ping", "-c", "1", "-W" if sys.platform != "darwin" else "-t", str(timeout_s), target],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_s + 2,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    m = _RTT_RE.search(out)
    return float(m.group(1)) if m else 0.0


def check_connection(target: str, timeout_s: int = 3) -> bool:
    return ping_rtt(target, timeout_s) is not None


def sample_signal(client: RouterClient) -> None:
    """Record one signal reading to the history file; never raises."""
    try:
        client.login()
        try:
            info = client.sys_info()
        finally:
            client.logout()
    except Exception as e:  # noqa: BLE001 - sampling must never kill the daemon
        log.debug("Signal sample failed: %s", e)
        return
    rec = {"k": "sig"}
    for key in ("RSRP", "SINR", "RSRQ", "RSSI"):
        try:
            rec[key.lower()] = float(info.get(key, ""))
        except ValueError:
            pass
    history.append(rec)


def recover(client: RouterClient, escalate: bool) -> None:
    client.login()
    try:
        if escalate:
            log.warning("Fast reconnect didn't clear the outage last time; rebooting.")
            history.append({"k": "event", "what": "reboot"})
            client.reboot()
        else:
            log.warning("Heavy traffic lockup detected. Triggering fast reconnect...")
            history.append({"k": "event", "what": "reconnect"})
            client.reconnect()
    finally:
        client.logout()


def monitor_loop() -> None:
    if not settings.is_configured():
        sys.exit("Not configured yet. Run `airtel init` first.")

    client = RouterClient(
        base_url=settings.ROUTER_URL,
        username=settings.ROUTER_USER,
        password=settings.ROUTER_PASS,
    )

    consecutive_failures = 0
    last_action_escalated = False
    log.info(
        "Monitoring %s every %ss (threshold=%s failures)",
        settings.PING_TARGET,
        settings.CHECK_INTERVAL_SECONDS,
        settings.FAIL_THRESHOLD,
    )

    while True:
        if check_connection(settings.PING_TARGET):
            if consecutive_failures:
                log.info("Connection recovered.")
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            log.info("Ping failed (%s/%s)", consecutive_failures, settings.FAIL_THRESHOLD)

        if consecutive_failures >= settings.FAIL_THRESHOLD:
            try:
                recover(client, escalate=last_action_escalated)
                last_action_escalated = not last_action_escalated
            except (RouterAuthError, RouterAPIError) as e:
                log.error("Recovery action failed: %s", e)
            consecutive_failures = 0
            time.sleep(settings.RECOVERY_GRACE_SECONDS)

        time.sleep(settings.CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        monitor_loop()
    except KeyboardInterrupt:
        log.info("Stopped.")
