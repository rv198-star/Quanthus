"""Task-local operational API mail; no data imports or notification service."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib import request

EVENT_KEYS = {
    "kind",
    "run_id",
    "role",
    "market_date",
    "status",
    "reason",
    "observed_at",
    "pipeline_revision",
    "manifests",
    "execution_authorized",
}
STATUSES = {"complete", "partial", "closed", "busy", "failed"}


def atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate(value: dict[str, Any]) -> None:
    if (
        set(value) != EVENT_KEYS
        or value["kind"] != "p10-container-job-event-v1"
        or value["role"] not in {"daily", "recovery", "weekly"}
        or value["status"] not in STATUSES
        or value["execution_authorized"] is not False
        or not re.fullmatch(r"[a-z0-9-]{1,100}", str(value["run_id"]))
        or not re.fullmatch(r"[A-Z0-9_]{1,100}", str(value["reason"]))
        or not re.fullmatch(r"[0-9a-f]{40}", str(value["pipeline_revision"]))
        or not isinstance(value["manifests"], list)
        or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", str(h)) for h in value["manifests"])
    ):
        raise ValueError("P10_OPERATIONAL_EVENT_INVALID")
    date.fromisoformat(value["market_date"])
    if datetime.fromisoformat(value["observed_at"]).tzinfo is None:
        raise ValueError("P10_EVENT_CLOCK_REQUIRES_ZONE")
    if value["status"] == "complete" and not value["manifests"]:
        raise ValueError("P10_COMPLETE_EVENT_REQUIRES_VERIFIED_MANIFEST")


def deliver(event: Path, state: Path, secret: Path, *, attempts: int = 2) -> str:
    """API idempotency plus a durable local receipt, independent of Docker."""
    if event.is_symlink() or event.stat().st_size > 8192:
        raise ValueError("P10_MAIL_SMALL_PHYSICAL_EVENT_REQUIRED")
    value = json.loads(event.read_bytes())
    validate(value)
    if attempts not in {1, 2}:
        raise ValueError("P10_MAIL_BOUNDED_ATTEMPTS_REQUIRED")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    key = hashlib.sha256(encoded).hexdigest()
    if state.resolve() != state.absolute():
        raise ValueError("P10_MAIL_PHYSICAL_STATE_REQUIRED")
    state.mkdir(parents=True, exist_ok=True)
    with (state / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = state / (key + ".json")
        if receipt.exists() and json.loads(receipt.read_bytes()).get("status") == "sent":
            return "already_sent"
        # Runtime injection only: never bake a key into image ENV/ARG/layers.
        # Legacy file input remains readable for earlier independent send receipts.
        if "QUANTHUS_RESEND_API_KEY" in os.environ:
            settings = {
                "api_key": os.environ["QUANTHUS_RESEND_API_KEY"],
                "sender": os.environ.get("QUANTHUS_RESEND_FROM", "report@mindthus.com"),
                "recipients": [
                    v.strip()
                    for v in os.environ.get("QUANTHUS_RESEND_TO", "61718@qq.com").split(",")
                ],
            }
        else:
            settings = json.loads(secret.read_bytes())
        timeout = int(os.environ.get("QUANTHUS_RESEND_TIMEOUT_SECONDS", "15"))
        attempts = int(os.environ.get("QUANTHUS_RESEND_RETRIES", str(attempts - 1))) + 1
        if not 1 <= timeout <= 30 or attempts not in {1, 2}:
            raise ValueError("P10_MAIL_NOTIFICATION_BUDGET_INVALID")
        if (
            set(settings) != {"api_key", "sender", "recipients"}
            or not isinstance(settings["api_key"], str)
            or not settings["api_key"]
            or not isinstance(settings["sender"], str)
            or not settings["sender"]
            or not isinstance(settings["recipients"], list)
            or not 1 <= len(settings["recipients"]) <= 10
            or any(not isinstance(x, str) or "@" not in x for x in settings["recipients"])
        ):
            raise ValueError("P10_MAIL_PRIVATE_SETTINGS_INVALID")
        payload = {
            "from": settings["sender"],
            "to": settings["recipients"],
            "subject": ("[通道验收测试] " if value["reason"].startswith("ACCEPTANCE_") else "")
            + f"Quanthus {value['role']} {value['market_date']}: {value['status']}",
            "text": encoded.decode(),
        }
        for attempt in range(attempts):
            try:
                req = request.Request(
                    "https://api.resend.com/emails",
                    data=json.dumps(payload).encode(),
                    headers={
                        "Authorization": "Bearer " + settings["api_key"],
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "Quanthus-Warehouse-Notifier/1.0",
                        "Idempotency-Key": key,
                    },
                    method="POST",
                )
                with request.urlopen(req, timeout=timeout) as response:
                    reply = json.loads(response.read(8192))
                if not isinstance(reply.get("id"), str) or not reply["id"]:
                    raise ValueError("P10_MAIL_PROVIDER_RECEIPT_MISSING")
                atomic(receipt, {"status": "sent", "event_sha256": key, "attempts": attempt + 1})
                return "sent"
            except Exception:
                # Never retain API responses, exception strings or credential contents.
                atomic(receipt, {"status": "failed", "event_sha256": key, "attempts": attempt + 1})
                if attempt + 1 < attempts:
                    time.sleep(1)
        raise RuntimeError("P10_OPERATIONAL_MAIL_FAILED_EVENT_RETAINED")


def notify(event: Path, state: Path, secret: Path) -> str:
    """Notification failure never changes the sealed data job's disposition."""
    try:
        return deliver(event, state, secret)
    except Exception:
        return "failed_event_retained"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    if args.event.parent.resolve() != Path("/run/events"):
        raise ValueError("P10_MAIL_EVENT_DESTINATION_INVALID")
    result = notify(args.event, Path("/run/mail"), Path("/run/secrets/mail.json"))
    print(json.dumps({"mail": result}))
    if result == "failed_event_retained":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
