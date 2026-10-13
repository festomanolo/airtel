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
