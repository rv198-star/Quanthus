"""Original responses are persisted before interpretation; clocks are not PIT claims."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

POLICY = "p10-acquisition-prevention-v1"


def enabled() -> bool:
    value = os.environ.get("HERMES_ODS_PREVENTION_POLICY", "")
    if value and value != POLICY:
        raise ValueError("unknown acquisition prevention policy")
    return value == POLICY


def clock() -> str:
    return datetime.now(timezone.utc).isoformat()


def decimal_token(value: Any, *, required: bool = False) -> str | None:
    """Keep supplier spelling and exact value. Missing is never an invented zero."""
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError("REQUIRED_NUMERIC_TOKEN_MISSING")
        return None
    text = str(value)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("INVALID_NUMERIC_TOKEN") from exc
    if not number.is_finite():
        raise ValueError("NONFINITE_NUMERIC_TOKEN")
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -10 or number.adjusted() >= 28:
        raise ValueError("DECIMAL_SERIALIZATION_LOSS")
    return text


def capture(
    *,
    request: dict[str, Any],
    raw: bytes,
    started_at: str,
    completed_at: str,
    status: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """A SDK capture is lossless SDK tokens, explicitly not invented wire bytes."""
    if root is None:
        configured = os.environ.get("HERMES_ODS_RAW_CAPTURE_ROOT")
        if not configured:
            if enabled():
                raise ValueError("RAW_CAPTURE_ROOT_MISSING")
            return {}
        root = Path(configured)
    envelope = {
        "policy_id": POLICY,
        "request": request,
        "status": status,
        "request_started_at": started_at,
        "response_completed_at": completed_at,
        "warehouse_observed_at": clock(),
        "publication_at": None,
        "provider_availability_at": None,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "raw_base64": base64.b64encode(raw).decode("ascii"),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    identity = hashlib.sha256(encoded).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{identity}.json.gz"
    with path.open("xb") as handle:
        handle.write(gzip.compress(encoded, mtime=0))
        handle.flush()
        os.fsync(handle.fileno())
    return {key: value for key, value in envelope.items() if key != "raw_base64"} | {
        "capture_file": path.name,
        "capture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def verify_capture(path: Path) -> dict[str, Any]:
    envelope: dict[str, Any] = json.loads(gzip.decompress(path.read_bytes()))
    raw = base64.b64decode(envelope["raw_base64"], validate=True)
    if (
        hashlib.sha256(raw).hexdigest() != envelope["raw_sha256"]
        or len(raw) != envelope["raw_bytes"]
    ):
        raise ValueError("RAW_CLOSURE_MISMATCH")
    if datetime.fromisoformat(envelope["response_completed_at"]) < datetime.fromisoformat(
        envelope["request_started_at"]
    ):
        raise ValueError("RESPONSE_CLOCK_REGRESSION")
    return envelope


def suspended_status(
    row: list[str], *, code: str, provider_code: str, day: str, observed_at: str
) -> dict[str, Any] | None:
    """A proven suspension may lack volume/amount; never invent a price bar."""
    if len(row) < 12 or row[0] != day or row[1] != provider_code:
        raise ValueError("MARKET_RESPONSE_IDENTITY_DIFFERS")
    if row[10] not in {"0", "1"} or row[11] not in {"0", "1"}:
        raise ValueError("MARKET_STATUS_MISSING")
    if row[10] != "0" or all(token.strip() for token in row[2:8]):
        return None
    for token in row[2:8]:
        decimal_token(token)
    stamp = datetime.fromisoformat(observed_at)
    if stamp.tzinfo is None:
        raise ValueError("SUSPENSION_CLOCK_NAIVE")
    return {
        "security_id": code,
        "stock_code": code,
        "market_date": day,
        "date": day,
        "tradestatus": "0",
        "isST": row[11],
        "source": "baostock.query_history_k_data_plus",
        "acquired_at": observed_at,
        "built_at": observed_at,
    }
