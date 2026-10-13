"""
Direct JSON-API client for the Tozed/ZLT X25 (Airtel-branded) router web UI.

The firmware's web app (Vue + jQuery) talks to /cgi-bin/http.cgi with small
JSON payloads keyed by a numeric `cmd`. This client replays that protocol
directly instead of driving a browser, which is both faster and far more
reliable than clicking through the DOM.

Protocol notes (reverse-engineered from the router's own js/main.js):
  - cmd 232  GET_NEXT_LOGIN_TIME  -> returns a one-time `token` used to hash
             the login password: passwd = sha256(token + plaintext_password)
  - cmd 100  LOGIN   (POST)       -> body includes username, passwd (hashed
             above), and a random sessionId. Response includes a `sessionId`
             to use as a cookie/field on every subsequent call.
  - cmd 233  RESET_LOGIN_TIME     -> returns a short-lived `token` required
             on the `token` field of privileged POST actions.
  - cmd 205  SYS_INFO_NETWORK     -> live signal/cell/throughput stats.
  - cmd 99   SYS_REBOOT   (POST)  -> reboots the device.
  - cmd 48   NET_CONNECT  (POST)  -> reconnect the WAN/cellular link.
  - cmd 49   NET_DISCONNECT (POST) -> drop the WAN/cellular link.

Only login-level (not root/EDL) access is available with a normal admin
account; some commands may return LIMITED_ACCESS depending on account level.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

# cmd ids pulled from the router's own main.js RequestCmd map
CMD_GET_NEXT_LOGIN_TIME = 232
CMD_RESET_LOGIN_TIME = 233
CMD_LOGIN = 100
CMD_LOGOUT = 101
CMD_SYS_INFO_NETWORK = 205
CMD_SYS_REBOOT = 6
CMD_REBOOT = 99
CMD_NET_CONNECT = 48
CMD_NET_DISCONNECT = 49
CMD_SMS_INFO = 12
CMD_SMS_SEND = 13
CMD_SMS_DELETE = 14
CMD_MAC_FILTER = 23
CMD_MAC_DEFAULT_FILTER = 30
CMD_DHCPCLIENT_INFO = 223
CMD_WIFI24G_LIST_INFO = 224
CMD_WIFI5G_LIST_INFO = 225


class RouterAuthError(RuntimeError):
    """Raised when login fails or the session is rejected."""


class RouterAPIError(RuntimeError):
    """Raised when the router returns success: false for a command."""


def _random_session_id() -> str:
    def md5_rand() -> str:
        return hashlib.md5(str(random.random()).encode()).hexdigest()

    return md5_rand() + md5_rand()


@dataclass
class RouterClient:
    base_url: str = "http://192.168.1.1"
    username: str = "admin"
    password: str = ""
    timeout: float = 8.0

    session_id: Optional[str] = None
    _http: requests.Session = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._http = requests.Session()

    @property
    def cgi_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/cgi-bin/http.cgi"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(self.cgi_url, data=json.dumps(payload), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def login(self) -> None:
        """Authenticate and store the session id for subsequent calls."""
        token_resp = self._post(
            {
                "cmd": CMD_GET_NEXT_LOGIN_TIME,
                "method": "GET",
                "language": "EN",
                "sessionId": "",
            }
        )
        login_token = token_resp.get("token", "")

        session_id = _random_session_id()
        hashed_pw = hashlib.sha256((login_token + self.password).encode()).hexdigest()

        login_resp = self._post(
            {
                "cmd": CMD_LOGIN,
                "method": "POST",
                "language": "EN",
                "sessionId": session_id,
                "username": self.username,
                "passwd": hashed_pw,
                "isAutoUpgrade": "0",
                "token": login_token,
            }
        )

        if login_resp.get("login_fail") == "fail" or not login_resp.get("sessionId"):
            raise RouterAuthError(f"Login failed: {login_resp}")

        self.session_id = login_resp["sessionId"]

    def _action_token(self) -> str:
        """Fetch a fresh action token required by privileged POST commands."""
        resp = self._post(
            {
                "cmd": CMD_RESET_LOGIN_TIME,
                "method": "GET",
                "language": "EN",
                "sessionId": self.session_id,
            }
        )
        return resp.get("token", "")

    def _ensure_session(self) -> None:
        if not self.session_id:
            self.login()

    def call(self, cmd: int, method: str = "GET", **extra: Any) -> dict[str, Any]:
        """Low-level helper to call an arbitrary cmd id, auto-logging in first."""
        self._ensure_session()
        payload = {
            "cmd": cmd,
            "method": method,
            "language": "EN",
            "sessionId": self.session_id,
        }
        if method == "POST":
            payload["token"] = self._action_token()
        payload.update(extra)

        result = self._post(payload)
        if result.get("success") is False:
            raise RouterAPIError(f"cmd {cmd} failed: {result.get('message')}")
        return result

    def sys_info(self) -> dict[str, Any]:
        """Live signal/cell/throughput stats (works at normal admin level)."""
        return self.call(CMD_SYS_INFO_NETWORK, method="GET")

    def reconnect(self) -> None:
        """Toggle the WAN/cellular connection off then on (fast path)."""
        self.call(CMD_NET_DISCONNECT, method="POST")
        time.sleep(5)
        self.call(CMD_NET_CONNECT, method="POST")

    def reboot(self) -> None:
        """Full device reboot (slow path, ~60-90s to come back)."""
        self.call(CMD_REBOOT, method="POST")

    def logout(self) -> None:
        if self.session_id:
            try:
                self.call(CMD_LOGOUT, method="POST")
            except RouterAPIError:
                pass
            self.session_id = None
