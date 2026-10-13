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
