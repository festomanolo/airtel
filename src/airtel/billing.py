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
