"""Loopback refresh adapter for the existing CN02 Duplicati Drive credential.

Only app-created archive folders are writable (drive.file). No scope expansion.
Never print provider responses, auth IDs, refresh secrets or bearer tokens.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import parse, request

from quanthus.pipelines.p10_complete_daily_gdrive import POLICY_PATH
from quanthus.pipelines.p10_delivery_common import object_at

SECRETS = Path("/etc/quanthus/gdrive-complete-v1/oauth-bridge.json")


class Tokens:
    def __init__(self) -> None:
        self.secrets = object_at(SECRETS)
        self.policy = object_at(POLICY_PATH)
        self.cache: dict[str, Any] = {}
        self.lock = threading.Lock()

    def refresh(self) -> dict[str, Any]:
        with self.lock:
            if self.cache.get("expires_at", 0) < time.time() + 120:
                for route in (self.policy["primary_proxy"], self.policy["fallback_proxy"]):
                    opener = request.build_opener(
                        request.ProxyHandler({"http": route["url"], "https": route["url"]})
                    )
                    req = request.Request(
                        self.secrets["oauth_url"],
                        headers={
                            "X-AuthID": self.secrets["authid"],
                            "User-Agent": "Duplicati/2.4.0",
                        },
                    )
                    try:
                        with opener.open(req, timeout=40) as response:
                            value = json.load(response)
                        self.cache = {
                            "access_token": value["access_token"],
                            "expires_at": time.time() + int(value["expires"]),
                        }
                        break
                    except Exception:
                        if route == self.policy["fallback_proxy"]:
                            raise
            return {
                "access_token": self.cache["access_token"],
                "token_type": "Bearer",
                "expires_in": max(1, int(self.cache["expires_at"] - time.time() - 30)),
                "refresh_token": self.secrets["refresh_secret"],
            }


def main() -> None:
    tokens = Tokens()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def reply(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/token" or not 0 < length <= 8192:
                    self.reply(400, {"error": "invalid_request"})
                    return
                fields = parse.parse_qs(self.rfile.read(length).decode())
                if fields.get("grant_type") != ["refresh_token"] or not hmac.compare_digest(
                    fields.get("refresh_token", [""])[0], tokens.secrets["refresh_secret"]
                ):
                    self.reply(401, {"error": "invalid_grant"})
                    return
                self.reply(200, tokens.refresh())
            except Exception:
                self.reply(502, {"error": "temporarily_unavailable"})

    ThreadingHTTPServer(("127.0.0.1", 3397), Handler).serve_forever()


if __name__ == "__main__":
    main()
