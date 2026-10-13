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
import subprocess
import sys
import time

from . import settings
from .router_client import RouterAPIError, RouterAuthError, RouterClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")


def check_connection(target: str, timeout_s: int = 3) -> bool:
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W" if sys.platform != "darwin" else "-t", str(timeout_s), target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=timeout_s + 2,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def recover(client: RouterClient, escalate: bool) -> None:
    client.login()
    try:
        if escalate:
            log.warning("Fast reconnect didn't clear the outage last time; rebooting.")
            client.reboot()
        else:
            log.warning("Heavy traffic lockup detected. Triggering fast reconnect...")
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
