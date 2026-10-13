"""Loads settings for the airtel CLI.

Resolution order:
  1. ~/.config/airtel/.env   (written by `airtel init`; the normal case
     once installed as a CLI — override the directory with AIRTEL_CONFIG_DIR)
  2. a local .env in the current directory (handy for development)
  3. plain environment variables

Nothing here defaults to a specific person's router password — ROUTER_URL
and ROUTER_USER fall back to the router firmware's own generic defaults
(192.168.1.1 / admin), and ROUTER_PASS has no default at all. Run
`airtel init` to set real values for your device.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR = Path(os.environ.get("AIRTEL_CONFIG_DIR", Path.home() / ".config" / "airtel"))
ENV_FILE = CONFIG_DIR / ".env"
PID_FILE = CONFIG_DIR / "monitor.pid"
LOG_FILE = CONFIG_DIR / "monitor.log"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    load_dotenv()  # local .env in cwd, if present (development mode)

ROUTER_URL = os.getenv("ROUTER_URL", "http://192.168.1.1")
ROUTER_USER = os.getenv("ROUTER_USER", "admin")
ROUTER_PASS = os.getenv("ROUTER_PASS", "")

PING_TARGET = os.getenv("PING_TARGET", "8.8.8.8")
FAIL_THRESHOLD = int(os.getenv("FAIL_THRESHOLD", "3"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "10"))
RECOVERY_GRACE_SECONDS = int(os.getenv("RECOVERY_GRACE_SECONDS", "45"))


def is_configured() -> bool:
    return bool(ROUTER_PASS)
