"""`airtel billing ...` and `airtel probe` commands."""
import argparse
import json
import sys
import time
from typing import Any

from rich.table import Table

from . import billing, settings, ui
from .router_client import RouterAPIError, RouterAuthError

console = ui.console
DB_FILE = settings.CONFIG_DIR / "billing.db"


def _store() -> billing.Store:
    return billing.Store(DB_FILE)


def cmd_probe(args: argparse.Namespace) -> None:
    """Read-only: GET a cmd id and print the raw JSON, to learn payload shapes."""
    from .cli import _client

    extra = dict(kv.split("=", 1) for kv in args.params)
    client = _client()
    try:
        client.login()
        console.print_json(json.dumps(client.call(args.cmd, method="GET", **extra)))
    except (RouterAuthError, RouterAPIError) as e:
        ui.fail(str(e))
        sys.exit(1)
    finally:
        client.logout()


def cmd_plan(args: argparse.Namespace) -> None:
    s = _store()
    if args.op == "add":
        s.add_plan(args.name, args.price, args.minutes)
        ui.ok(f"Plan {args.name}: {args.price:g} for {args.minutes} min")
        return
    t = Table("plan", "price", "minutes")
    for p in s.plans():
        t.add_row(p["name"], f"{p['price']:g}", str(p["minutes"]))
    console.print(t)


def cmd_customer(args: argparse.Namespace) -> None:
    s = _store()
    try:
        s.add_customer(args.phone, args.mac, args.name)
    except ValueError as e:
        ui.fail(str(e))
        sys.exit(1)
    phone = billing.normalize_phone(args.phone)
    n = billing.claim_unmatched(s, phone)
    ui.ok(f"{phone} → {billing.normalize_mac(args.mac)}" + (f" (applied {n} earlier payment(s))" if n else ""))


def cmd_parse(args: argparse.Namespace) -> None:
    pay = billing.parse_payment(args.sms)
    if pay:
        ui.ok(f"txn {pay.txn} · {pay.phone} · {pay.amount:g}")
    else:
        ui.fail("Not recognised as a payment. Adjust the patterns in billing.py.")
        sys.exit(1)


def cmd_payments(_args: argparse.Namespace) -> None:
    t = Table("when", "txn", "phone", "amount", "plan", "status")
    for r in _store().db.execute("SELECT * FROM payments ORDER BY ts DESC LIMIT 30"):
        t.add_row(time.strftime("%m-%d %H:%M", time.localtime(r["ts"])), r["txn"], r["phone"],
                  f"{r['amount']:g}", r["plan"] or "-", r["status"])
    console.print(t)


class _DryRouter:
    """Reads the real SMS inbox; only prints allow/revoke instead of applying."""

    def __init__(self, real: Any) -> None:
        self.real = real

    def inbox(self) -> list[dict[str, Any]]:
        return self.real.inbox()

    def allow(self, mac: str) -> None:
        console.print(f"[dim]dry-run allow  {mac}[/dim]")

    def revoke(self, mac: str) -> None:
        console.print(f"[dim]dry-run revoke {mac}[/dim]")


def cmd_run(args: argparse.Namespace) -> None:
    from .cli import _client

    store = _store()
    client = _client()
    real = billing.RouterBackend(client)
    backend = _DryRouter(real) if args.dry_run else real
    try:
        while True:
            try:
                client.login()
                rep = billing.tick(store, backend)
                client.logout()
            except (RouterAuthError, RouterAPIError, OSError) as e:
                ui.fail(f"router: {e}")
                client.session_id = None
            else:
                if rep.granted or rep.unmatched or rep.revoked:
                    ui.ok(f"granted {len(rep.granted)} · unmatched {len(rep.unmatched)} · expired {len(rep.revoked)}")
                for err in rep.errors:
                    ui.fail(err)
                if rep.errors:
                    sys.exit(1)
            if args.once:
