"""Small operating receipts let downstream waits reuse an acknowledged root alert."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at

CN01_NOTES = Path("/srv/quanthus/dwd-dws-v2/intraday/operations/complete-daily-v1/notifications")
EXPORT = Path("/srv/quanthus/dwd-dws-v2/daily/products/complete-daily/notification-incidents")
STAGES = ("daily_ods", "minutes", "complete")
KIND = "p10_acknowledged_upstream_notification_v1"


def seal(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "receipt_digest": digest(encoded(value))}


def valid(value: dict[str, Any], day: date, before_stage: str) -> bool:
    if before_stage not in (*STAGES, "mirror"):
        return False
    allowed = STAGES[: (*STAGES, "mirror").index(before_stage)]
    raw = {key: val for key, val in value.items() if key != "receipt_digest"}
    try:
        stamp = datetime.fromisoformat(value["sent_at"])
        return (
            value.get("kind") == KIND
            and value.get("market_date") == str(day)
            and value.get("stage") in allowed
            and value.get("resolved") is False
            and value.get("delivery_status") == "sent"
            and bool(value.get("idempotency_key"))
            and stamp.tzinfo is not None
            and stamp <= datetime.now(timezone.utc)
            and value.get("receipt_digest") == digest(encoded(raw))
        )
    except (TypeError, KeyError, ValueError):
        return False


def acknowledge(root: Path, day: date, stage: str, receipt: dict[str, Any]) -> None:
    if root != CN01_NOTES or stage not in STAGES:
        return
    if receipt.get("delivery_status") != "sent":
        return
    if str(receipt.get("market_date_label", "")).startswith("运行日期"):
        return
    prior = EXPORT / f"{day}-{stage}.json"
    if prior.exists() and object_at(prior).get("resolved") is True:
        return
    EXPORT.mkdir(mode=0o2750, exist_ok=True)
    if EXPORT.is_symlink():
        raise ValueError("notification incident export may not be a symlink")
    os.chown(EXPORT, -1, EXPORT.parent.stat().st_gid)
    os.chmod(EXPORT, 0o2750)
    atomic(
        EXPORT / f"{day}-{stage}.json",
        seal(
            {
                "kind": KIND,
                "market_date": str(day),
                "stage": stage,
                "resolved": False,
                "delivery_status": "sent",
                "sent_at": receipt["sent_at"],
                "idempotency_key": receipt["idempotency_key"],
            }
        ),
    )


def resolve(root: Path, day: date, stage: str) -> None:
    if root != CN01_NOTES or stage not in STAGES:
        return
    path = EXPORT / f"{day}-{stage}.json"
    if path.exists():
        value = object_at(path)
        value.pop("receipt_digest", None)
        atomic(
            path,
            seal(
                {**value, "resolved": True, "resolved_at": datetime.now(timezone.utc).isoformat()}
            ),
        )


def upstream(root: Path, day: date, stage: str) -> dict[str, Any] | None:
    for source in STAGES:
        path = root / f"{day}-{source}.json"
        if path.is_file() and path.stat().st_size <= 16384:
            try:
                value = object_at(path)
                if valid(value, day, stage):
                    return value
            except (OSError, ValueError, TypeError):
                continue
    return None


def recent_remote(root: Path, day: date) -> dict[str, Any] | None:
    try:
        proof = object_at(root / "fresh-receipt.json")
        stamp = datetime.fromisoformat(proof["fetched_at"])
        elapsed = (datetime.now(timezone.utc) - stamp).total_seconds()
        value = proof["incident"]
        if (
            isinstance(value, dict)
            and proof.get("market_date") == str(day)
            and 0 <= elapsed <= 300
            and valid(value, day, "mirror")
        ):
            return value
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None
