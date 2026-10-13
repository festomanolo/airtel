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


# ------------------------------------------------------------------- commands


def cmd_init(_args: argparse.Namespace) -> None:
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    console.print(
        Panel(
            "Enter your router's details. Press Enter to accept the value in [brackets].\n"
            "The login is verified before anything is saved.",
            title=Text("airtel setup", style="bold"),
            title_align="left",
            border_style="grey50",
            box=box.ROUNDED,
        )
    )
    raw_url = console.input(f"[bold]Router URL or IP[/bold] [dim]\\[{settings.ROUTER_URL}][/dim]: ").strip()
    url = _normalize_url(raw_url) if raw_url else settings.ROUTER_URL
    user = console.input(f"[bold]Username[/bold] [dim]\\[{settings.ROUTER_USER}][/dim]: ").strip() or settings.ROUTER_USER
    password = getpass.getpass("Password: ").strip()
    if not password:
        ui.fail("A password is required.")
        sys.exit(1)

    client = RouterClient(base_url=url, username=user, password=password)
    try:
        with console.status(f"Verifying login at {url}…", spinner="dots"):
            client.login()
            client.logout()
    except (RouterAuthError, RouterAPIError) as e:
        ui.fail(f"Login check failed, nothing saved: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - network errors surface as many types
        ui.fail(f"Could not reach {url}: {e}")
        sys.exit(1)

    lines = [
        f"ROUTER_URL={url}",
        f"ROUTER_USER={user}",
        f"ROUTER_PASS={password}",
        f"PING_TARGET={settings.PING_TARGET}",
        f"FAIL_THRESHOLD={settings.FAIL_THRESHOLD}",
        f"CHECK_INTERVAL_SECONDS={settings.CHECK_INTERVAL_SECONDS}",
        f"RECOVERY_GRACE_SECONDS={settings.RECOVERY_GRACE_SECONDS}",
        f"SIGNAL_SAMPLE_SECONDS={settings.SIGNAL_SAMPLE_SECONDS}",
    ]
    settings.ENV_FILE.write_text("\n".join(lines) + "\n")
    settings.ENV_FILE.chmod(0o600)
    ui.ok(f"Login OK. Saved to [dim]{settings.ENV_FILE}[/dim]")
    ui.info_line("Try [bold]airtel status[/bold], or run [bold]airtel[/bold] to start background monitoring.")


def _series(records: list[dict[str, Any]], key: str) -> list[Optional[float]]:
    return [r.get(key) for r in records]


def _span(records: list[dict[str, Any]]) -> str:
    if len(records) < 2:
        return ""
    return f"last {ui.human_duration(records[-1]['t'] - records[0]['t'])}"


def _history_views() -> dict[str, Any]:
    recs = history.load(since=time.time() - 3600)
    sigs = [r for r in recs if r.get("k") == "sig"][-60:]
    pings = [r for r in recs if r.get("k") == "ping"][-60:]
    return {
        "signal_series": {m.key: _series(sigs, m.key.lower()) for m in ui.METRICS},
        "signal_span": _span(sigs),
        "rtts": _series(pings, "rtt"),
        "rtt_span": _span(pings),
    }


def cmd_status(args: argparse.Namespace) -> None:
    if args.json:
        client = _client()
        try:
            client.login()
            print(json.dumps(client.sys_info(), indent=2))
        except (RouterAuthError, RouterAPIError) as e:
            ui.fail(str(e))
            sys.exit(1)
        finally:
            client.logout()
        return

    if args.watch:
        return _status_watch(args.interval)

    client = _client()
    try:
        with console.status("Querying router…", spinner="dots"):
            client.login()
            info = client.sys_info()
    except (RouterAuthError, RouterAPIError) as e:
        ui.fail(str(e))
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        ui.fail(f"Could not reach {settings.ROUTER_URL}: {e}")
        sys.exit(1)
    finally:
        client.logout()

    views = _history_views()
    console.print(
        ui.dashboard(
            info,
            daemon=daemon_info(),
            ping_target=settings.PING_TARGET,
            footer="Graphs show watchdog history. Use `airtel status --watch` for live throughput.",
            **views,
        )
    )


def _status_watch(interval: float) -> None:
    from .monitor import ping_rtt

    client = _client()
    keep = 60
    sig = {m.key: deque(maxlen=keep) for m in ui.METRICS}
    rtts: deque = deque(maxlen=keep)
    dl_rates: deque = deque(maxlen=keep)
    ul_rates: deque = deque(maxlen=keep)
    counters: deque = deque(maxlen=5)  # (t, dl_kb, ul_kb); router counters tick every few seconds
    info: dict[str, Any] = {}
    error: Optional[str] = None
    start = time.monotonic()

    def render() -> Any:
        if not info:
            return Text("Connecting…", style="dim")
        n = len(rtts)
        span = f"last {ui.human_duration(n * interval)}" if n > 1 else ""
        return ui.dashboard(
            info,
            daemon=daemon_info(),
            signal_series=sig,
            signal_span=span,
            rtts=list(rtts),
            rtt_span=span,
            ping_target=settings.PING_TARGET,
            dl_rates=list(dl_rates),
            ul_rates=list(ul_rates),
            online=rtts[-1] is not None if rtts else None,
            footer=(f"✗ {error}" if error else f"Refreshing every {interval:g}s · running {ui.human_duration(time.monotonic() - start)} · Ctrl+C to exit"),
        )

    try:
        with Live(render(), console=console, screen=True, auto_refresh=False) as live:
            while True:
                try:
                    if not client.session_id:
                        client.login()
                    info = client.sys_info()
                    error = None
                except (RouterAuthError, RouterAPIError, Exception) as e:  # noqa: BLE001
                    client.session_id = None
                    error = f"router: {e}"
                else:
                    for m in ui.METRICS:
                        sig[m.key].append(ui.num(info.get(m.key)))
                    dl, ul = ui.num(info.get("flow_dl")), ui.num(info.get("flow_ul"))
                    now = time.monotonic()
                    if dl is not None and ul is not None:
                        counters.append((now, dl, ul))
                        if len(counters) > 1:
                            t0, d0, u0 = counters[0]
                            dt = max(now - t0, 1e-6)
                            dl_rates.append(max((dl - d0) / dt, 0.0))
                            ul_rates.append(max((ul - u0) / dt, 0.0))
                rtts.append(ping_rtt(settings.PING_TARGET, timeout_s=2))
                live.update(render(), refresh=True)
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.logout()


def cmd_reconnect(_args: argparse.Namespace) -> None:
    from .monitor import ping_rtt

    client = _client()
    try:
        with console.status("Logging in…", spinner="dots") as st:
            client.login()
            st.update("Toggling cellular link…")
            started = time.monotonic()
            client.reconnect()
            st.update("Waiting for the link to come back…")
            deadline = time.monotonic() + 40
            back = False
            while time.monotonic() < deadline:
                if ping_rtt(settings.PING_TARGET, timeout_s=2) is not None:
                    back = True
                    break
                time.sleep(1)
        took = ui.human_duration(time.monotonic() - started)
        if back:
            ui.ok(f"Link restored in {took}")
        else:
            ui.fail(f"Reconnect sent, but {settings.PING_TARGET} still isn't answering after {took}")
            sys.exit(1)
    except (RouterAuthError, RouterAPIError) as e:
        ui.fail(str(e))
        sys.exit(1)
    finally:
        client.logout()


def cmd_reboot(_args: argparse.Namespace) -> None:
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
