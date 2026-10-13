"""Loads settings from .env (see .env.example) with sane fallbacks."""
import os

from dotenv import load_dotenv

load_dotenv()

ROUTER_URL = os.getenv("ROUTER_URL", "http://192.168.1.1")
ROUTER_USER = os.getenv("ROUTER_USER", "admin")
ROUTER_PASS = os.getenv("ROUTER_PASS", "")

PING_TARGET = os.getenv("PING_TARGET", "8.8.8.8")
FAIL_THRESHOLD = int(os.getenv("FAIL_THRESHOLD", "3"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "10"))
RECOVERY_GRACE_SECONDS = int(os.getenv("RECOVERY_GRACE_SECONDS", "45"))
