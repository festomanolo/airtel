"""Pay-by-SMS access control for the router's Wi-Fi.

Flow: a customer pays mobile money to the SIM in the router -> the provider's
confirmation SMS lands in the router's inbox -> we parse amount, payer phone
and transaction id -> if the phone is registered to one or more device MACs,
those MACs are allowed on the router until the paid time runs out.

This module is router-agnostic: all router I/O goes through a `Backend`
(inbox / allow / revoke), so the whole flow is testable offline.

Security notes:
  * Anyone can text the SIM a fake "you have received..." message. Only SMS
    from `trusted_senders` (the provider's alphanumeric sender id) count.
  * Each transaction id is honoured once (replay protection).
  * Amount must match a plan; short payments are recorded but grant nothing.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

DEFAULT_TRUSTED_SENDERS = ("AIRTELMONEY", "MPESA", "M-PESA", "TIGOPESA", "HALOPESA", "TTCLMOBILE")

# Deliberately loose; real provider formats vary. Override per deployment by
# passing your own patterns to `parse_payment`. Verify against real SMS first
# (`airtel billing parse "<sms text>"`).
AMOUNT_RE = re.compile(r"(?:TSh|TZS|Tsh|TShs)\.?\s*([\d,]+(?:\.\d+)?)", re.I)
PHONE_RE = re.compile(r"(\+?255\d{9}|\b0\d{9}\b)")
TXN_RE = re.compile(r"(?:Trans(?:action)?\s*(?:ID|Id|No)|TxnId|Txn\s*ID|Ref(?:erence)?(?:\s*No)?)\s*[:#]?\s*([A-Z0-9]{6,})", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    name TEXT PRIMARY KEY, price REAL NOT NULL, minutes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS customers (
    phone TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY, phone TEXT NOT NULL REFERENCES customers(phone));
CREATE TABLE IF NOT EXISTS payments (
    txn TEXT PRIMARY KEY, phone TEXT NOT NULL, amount REAL NOT NULL, ts REAL NOT NULL,
    plan TEXT, status TEXT NOT NULL, raw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    phone TEXT PRIMARY KEY, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sms_seen (key TEXT PRIMARY KEY);
"""


# ---------------------------------------------------------------- parsing

def normalize_phone(raw: str) -> Optional[str]:
    """Return a Tanzanian number as 255XXXXXXXXX, or None if it doesn't fit."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "255" + digits[1:]
    return digits if len(digits) == 12 and digits.startswith("255") else None


def normalize_mac(raw: str) -> str:
    hexes = re.sub(r"[^0-9a-fA-F]", "", raw or "")
    if len(hexes) != 12:
        raise ValueError(f"not a MAC address: {raw!r}")
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2)).upper()


@dataclass
class Payment:
    txn: str
    phone: str
    amount: float
    raw: str


def parse_payment(body: str) -> Optional[Payment]:
    """Extract (txn, payer phone, amount) from a received-money SMS, or None."""
    low = body.lower()
    if not any(w in low for w in ("received", "umepokea", "imepokelewa", "deposit", "credited")):
        return None
    amount = AMOUNT_RE.search(body)
    txn = TXN_RE.search(body)
    if not (amount and txn):
        return None
    phone = next((p for m in PHONE_RE.findall(body) if (p := normalize_phone(m))), None)
    if not phone:
        return None
    return Payment(txn=txn.group(1).upper(), phone=phone,
                   amount=float(amount.group(1).replace(",", "")), raw=body)


# ---------------------------------------------------------------- backends

class Backend(Protocol):
    def inbox(self) -> list[dict[str, Any]]: ...   # [{sender, body, ts}]
    def allow(self, mac: str) -> None: ...
    def revoke(self, mac: str) -> None: ...


class BackendUnverified(RuntimeError):
    """The router request for this action hasn't been captured yet."""


class DryRunBackend:
    """In-memory backend: records what would happen. Used for tests/`--dry-run`."""

    def __init__(self, messages: Iterable[dict[str, Any]] = ()) -> None:
        self.messages = list(messages)
        self.allowed: set[str] = set()

    def inbox(self) -> list[dict[str, Any]]:
        return list(self.messages)

    def allow(self, mac: str) -> None:
        self.allowed.add(mac)

    def revoke(self, mac: str) -> None:
        self.allowed.discard(mac)


class RouterBackend:
    """Live router. Reading SMS is read-only; allow/revoke write router config
    and stay disabled until the real MAC-filter request is captured (see
    docs/mac-whitelist.md) and implemented below."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def inbox(self) -> list[dict[str, Any]]:
        from .router_client import CMD_SMS_INFO
        return extract_messages(self.client.call(CMD_SMS_INFO, method="GET"))

    def allow(self, mac: str) -> None:
        raise BackendUnverified("MAC_FILTER (cmd 23) write payload not captured yet")

    def revoke(self, mac: str) -> None:
        raise BackendUnverified("MAC_FILTER (cmd 23) write payload not captured yet")


def extract_messages(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort: find the message list in an SMS_INFO response whatever it
    is called, and map common field names onto {sender, body, ts}. Unverified
    against a live inbox; `airtel probe 12` shows the real shape."""
    lists = [v for v in resp.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
    out = []
    for item in (lists[0] if lists else []):
        pick = lambda *keys: next((str(item[k]) for k in keys if k in item), "")  # noqa: E731
        out.append({
            "sender": pick("phone", "number", "from", "sender", "address"),
            "body": pick("content", "body", "msg", "message", "text"),
            "ts": pick("time", "date", "ts", "timestamp"),
        })
    return out


# ---------------------------------------------------------------- store

class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
