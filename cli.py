#!/usr/bin/env python3
"""Command-line entry point for one-off router actions.

Usage:
    python cli.py status       # print live signal/connection stats
    python cli.py reconnect    # fast WAN toggle (~10s)
    python cli.py reboot       # full device reboot (~60-90s)
"""
import json
import sys

import config
from router_client import RouterAPIError, RouterAuthError, RouterClient


def make_client() -> RouterClient:
    if not config.ROUTER_PASS:
        sys.exit("ROUTER_PASS is not set. Copy .env.example to .env and fill it in.")
    return RouterClient(
        base_url=config.ROUTER_URL,
        username=config.ROUTER_USER,
        password=config.ROUTER_PASS,
    )


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"status", "reconnect", "reboot"}:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    client = make_client()

    try:
        client.login()
        if action == "status":
            print(json.dumps(client.sys_info(), indent=2))
        elif action == "reconnect":
            print("[*] Toggling WAN/cellular connection...")
            client.reconnect()
            print("[+] Reconnected.")
        elif action == "reboot":
            print("[*] Issuing reboot command...")
            client.reboot()
            print("[+] Reboot triggered. Device will be unreachable for ~60-90s.")
    except RouterAuthError as e:
        sys.exit(f"[-] Auth failed: {e}")
    except RouterAPIError as e:
        sys.exit(f"[-] Router rejected the action: {e}")
    finally:
        client.logout()


if __name__ == "__main__":
    main()
