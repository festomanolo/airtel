"""Append-only JSONL history written by the monitor daemon, read by `airtel status`.

Record kinds (field "k"):
  ping   {"t", "rtt"}            rtt in ms, null on a missed ping
  sig    {"t", "rsrp", "sinr", "rsrq", "rssi"}
  event  {"t", "what"}           "reconnect" | "reboot"
"""
from __future__ import annotations

import json
import time
from typing import Any

from . import settings

RETENTION_SECONDS = 6 * 3600
_TRIM_EVERY = 1000
_appends = 0


def append(record: dict[str, Any]) -> None:
    global _appends
    record.setdefault("t", round(time.time(), 1))
    try:
        settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(settings.HISTORY_FILE, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        return
    _appends += 1
    if _appends % _TRIM_EVERY == 0:
        trim()


def load(since: float = 0.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(settings.HISTORY_FILE) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # torn write at the tail
                if rec.get("t", 0) >= since:
                    out.append(rec)
    except OSError:
        pass
    return out


def trim(max_age: float = RETENTION_SECONDS) -> None:
    keep = load(since=time.time() - max_age)
    try:
        tmp = settings.HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in keep))
        tmp.replace(settings.HISTORY_FILE)
    except OSError:
        pass
