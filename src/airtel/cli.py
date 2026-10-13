#!/usr/bin/env python3
"""airtel — background auto-recovery for Tozed/ZLT-style router web UIs
(e.g. an Airtel-branded ZLT X25).

Run `airtel help` for the command list.

Config is per-user, not per-project: `airtel init` writes to
~/.config/airtel/.env (override the directory with AIRTEL_CONFIG_DIR).
Nothing here ships with anyone's real router password baked in.
"""
import argparse
import getpass
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from typing import Any, Optional

from rich import box
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import history, settings, ui
from .router_client import RouterAPIError, RouterAuthError, RouterClient

console = ui.console

COMMANDS = [
    ("airtel", "Start the watchdog in the background"),
    ("airtel status", "Signal, cell, latency and traffic dashboard"),
    ("airtel status --watch", "Live dashboard with moving graphs (Ctrl+C to exit)"),
    ("airtel status --json", "Raw router payload as JSON"),
    ("airtel reconnect", "Toggle the cellular link (~10 s)"),
    ("airtel reboot", "Reboot the router (~60-90 s)"),
    ("airtel stop", "Stop the background watchdog"),
    ("airtel logs", "Follow the watchdog log (-n N for history)"),
    ("airtel foreground", "Run the watchdog attached, for debugging"),
    ("airtel init", "Set the router address and credentials"),
]


def _print_help() -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold cyan", no_wrap=True)
    t.add_column()
    for cmd, desc in COMMANDS:
        t.add_row(cmd, desc)
    console.print(
        Panel(
            t,
            title=Text("airtel · router watchdog for Tozed/ZLT (Airtel X25)", style="bold"),
            title_align="left",
            border_style="grey50",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def _need_config() -> None:
    if not settings.is_configured():
        ui.fail("Not configured yet. Run [bold]airtel init[/bold] first.")
        sys.exit(1)


def _client() -> RouterClient:
    _need_config()
    return RouterClient(
        base_url=settings.ROUTER_URL,
        username=settings.ROUTER_USER,
        password=settings.ROUTER_PASS,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid() -> Optional[int]:
    try:
        return int(settings.PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def daemon_info() -> dict[str, Any]:
    pid = _read_pid()
    if pid is None or not _pid_alive(pid):
        return {"running": False}
    events = [r for r in history.load(since=time.time() - 6 * 3600) if r.get("k") == "event"]
    return {
        "running": True,
        "pid": pid,
        "uptime": time.time() - settings.PID_FILE.stat().st_mtime,
        "last_event": events[-1] if events else None,
    }


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        return url
    if "://" not in url:
        url = f"http://{url}"
    return url


def cmd_init() -> None:
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print("Set up your router connection (press Enter to accept the default).")
    raw_url = input(f"Router URL or IP [{settings.ROUTER_URL}]: ").strip()
    url = _normalize_url(raw_url) if raw_url else settings.ROUTER_URL
    user = input(f"Username [{settings.ROUTER_USER}]: ").strip() or settings.ROUTER_USER
    password = getpass.getpass("Password: ").strip()
    if not password:
        sys.exit("A password is required.")

    print("Verifying login against the router before saving...")
    client = RouterClient(base_url=url, username=user, password=password)
    try:
        client.login()
        client.logout()
    except (RouterAuthError, RouterAPIError) as e:
        sys.exit(f"Login check failed, nothing saved: {e}")

    lines = [
        f"ROUTER_URL={url}",
        f"ROUTER_USER={user}",
        f"ROUTER_PASS={password}",
        f"PING_TARGET={settings.PING_TARGET}",
        f"FAIL_THRESHOLD={settings.FAIL_THRESHOLD}",
        f"CHECK_INTERVAL_SECONDS={settings.CHECK_INTERVAL_SECONDS}",
        f"RECOVERY_GRACE_SECONDS={settings.RECOVERY_GRACE_SECONDS}",
    ]
    settings.ENV_FILE.write_text("\n".join(lines) + "\n")
    settings.ENV_FILE.chmod(0o600)
    print(f"Login OK. Saved to {settings.ENV_FILE}.")
    print("Try `airtel status`, or just run `airtel` to start background monitoring.")


def cmd_status() -> None:
    client = _client()
    try:
        client.login()
        print(json.dumps(client.sys_info(), indent=2))
    except (RouterAuthError, RouterAPIError) as e:
        sys.exit(f"Error: {e}")
    finally:
        client.logout()


def cmd_reconnect() -> None:
    client = _client()
    try:
        client.login()
        print("Toggling WAN/cellular connection...")
        client.reconnect()
        print("Reconnected.")
    except (RouterAuthError, RouterAPIError) as e:
        sys.exit(f"Error: {e}")
    finally:
        client.logout()


def cmd_reboot() -> None:
    client = _client()
    try:
        client.login()
        print("Issuing reboot command...")
        client.reboot()
        print("Reboot triggered. Device will be unreachable for ~60-90s.")
    except (RouterAuthError, RouterAPIError) as e:
        sys.exit(f"Error: {e}")
    finally:
        client.logout()


def cmd_foreground() -> None:
    if not settings.is_configured():
        sys.exit("Not configured yet. Run `airtel init` first.")
    from .monitor import monitor_loop

    try:
        monitor_loop()
    except KeyboardInterrupt:
        pass


def cmd_start_background() -> None:
    if not settings.is_configured():
        sys.exit("Not configured yet. Run `airtel init` first.")
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if settings.PID_FILE.exists():
        pid = int(settings.PID_FILE.read_text().strip())
        if _pid_alive(pid):
            print(f"Already running (pid {pid}). Run `airtel stop` first to restart it.")
            return
        settings.PID_FILE.unlink()

    log_fh = open(settings.LOG_FILE, "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "airtel.cli", "foreground"],
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    settings.PID_FILE.write_text(str(proc.pid))
    print(f"Started in background (pid {proc.pid}).")
    print(f"Logs: airtel logs   (or tail -f {settings.LOG_FILE})")
    print("Stop with: airtel stop")


def cmd_stop() -> None:
    if not settings.PID_FILE.exists():
        print("Not running.")
        return
    pid = int(settings.PID_FILE.read_text().strip())
    if _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        print(f"Stopped (pid {pid}).")
    else:
        print("Not running (stale pid file cleared).")
    settings.PID_FILE.unlink()


def cmd_logs() -> None:
    if not settings.LOG_FILE.exists():
        sys.exit("No log file yet — the daemon hasn't run.")
    subprocess.run(["tail", "-f", str(settings.LOG_FILE)])


def main() -> None:
    args = sys.argv[1:]
    action = args[0] if args else "start"

    dispatch = {
        "start": cmd_start_background,
        "stop": cmd_stop,
        "foreground": cmd_foreground,
        "status": cmd_status,
        "reconnect": cmd_reconnect,
        "reboot": cmd_reboot,
        "init": cmd_init,
        "logs": cmd_logs,
        "-h": lambda: print(__doc__),
        "--help": lambda: print(__doc__),
    }

    fn = dispatch.get(action)
    if fn is None:
        print(f"Unknown command: {action}\n")
        print(__doc__)
        sys.exit(1)
    fn()


if __name__ == "__main__":
    main()
