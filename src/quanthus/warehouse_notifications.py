"""Idempotent operational email notifications for the DWD daily handoff."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from urllib import request
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse import (
    DailyDomainBuildError,
    verify_daily_domain_increment,
)


class WarehouseNotificationError(RuntimeError):
    """Raised when a warehouse completion notification cannot be delivered."""


EVENTS = {
    "cn01_dwd_completed": {
        "subject": "CN01 DWD 清洗完成",
        "heading": "CN01 DWD 清洗完成",
    },
    "oci_dwd_sync_completed": {
        "subject": "OCI DWD 增量同步完成",
        "heading": "OCI DWD 增量同步完成",
    },
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WarehouseNotificationError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise WarehouseNotificationError(f"JSON root is not an object: {path}")
    return value


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise WarehouseNotificationError(f"notification response field {key!r} is invalid")
    return item


def _package_from_current(
    *, daily_root: Path, market_date: date
) -> tuple[dict[str, object], object]:
    pointer = _object(daily_root / "CURRENT.json")
    if pointer.get("kind") != "cn01_daily_dwd_current":
        raise WarehouseNotificationError("DWD CURRENT kind is invalid")
    if pointer.get("market_date") != market_date.isoformat():
        raise WarehouseNotificationError("DWD CURRENT does not match the requested market date")
    raw_manifest = pointer.get("manifest_path")
    if not isinstance(raw_manifest, str):
        raise WarehouseNotificationError("DWD CURRENT manifest_path is invalid")
    manifest_path = Path(raw_manifest)
    if (
        manifest_path.is_absolute()
        or ".." in manifest_path.parts
        or len(manifest_path.parts) != 4
        or manifest_path.parts[0] != "packages"
        or manifest_path.parts[1] != market_date.isoformat()
        or manifest_path.parts[-1] != "manifest.json"
    ):
        raise WarehouseNotificationError("DWD CURRENT manifest_path is outside the daily package")
    package = (daily_root / manifest_path).resolve()
    if not package.is_relative_to(daily_root.resolve()):
        raise WarehouseNotificationError("DWD CURRENT manifest_path escapes the daily root")
    try:
        result = verify_daily_domain_increment(package.parent)
    except (DailyDomainBuildError, OSError, ValueError) as exc:
        raise WarehouseNotificationError(f"DWD package failed verification: {exc}") from exc
    if pointer.get("package_id") != result.package_id:
        raise WarehouseNotificationError("DWD CURRENT package_id differs from the manifest")
    if pointer.get("manifest_sha256") != result.manifest_sha256:
        raise WarehouseNotificationError("DWD CURRENT manifest_sha256 differs from the manifest")
    if pointer.get("content_digest") != result.content_digest:
        raise WarehouseNotificationError("DWD CURRENT content_digest differs from the manifest")
    return pointer, result


def _recipients() -> tuple[str, ...]:
    raw = os.environ.get("QUANTHUS_RESEND_TO", "61718@qq.com")
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise WarehouseNotificationError("QUANTHUS_RESEND_TO must contain at least one address")
    return values


def _email_html(
    *, event: str, market_date: date, pointer: Mapping[str, object], result: object
) -> str:
    details = [
        ("交易日", market_date.isoformat()),
        ("包 ID", str(result.package_id)),
        ("manifest SHA-256", str(result.manifest_sha256)),
        ("content digest", str(result.content_digest)),
        ("载荷大小", f"{result.payload_bytes} bytes"),
        ("行数", json.dumps(dict(result.row_counts), ensure_ascii=False, sort_keys=True)),
        ("CURRENT", str(pointer.get("manifest_path", "-"))),
    ]
    rows = "".join(
        f'<tr><th align="left">{html.escape(label)}</th><td>{html.escape(value)}</td></tr>'
        for label, value in details
    )
    return (
        "<!doctype html><html><body>"
        f"<h2>{html.escape(EVENTS[event]['heading'])}</h2>"
        f'<table border="1" cellpadding="6" cellspacing="0">{rows}</table>'
        "</body></html>"
    )


def _send_resend(
    *, event: str, market_date: date, pointer: Mapping[str, object], result: object, key: str
) -> dict[str, object]:
    api_key = os.environ.get("QUANTHUS_RESEND_API_KEY", "").strip()
    if not api_key:
        raise WarehouseNotificationError("QUANTHUS_RESEND_API_KEY is not set")
    api_url = os.environ.get("QUANTHUS_RESEND_API_URL", "https://api.resend.com/emails")
    sender = os.environ.get("QUANTHUS_RESEND_FROM", "report@mindthus.com").strip()
    if not sender:
        raise WarehouseNotificationError("QUANTHUS_RESEND_FROM is empty")
    retries_raw = os.environ.get("QUANTHUS_RESEND_RETRIES", "2")
    try:
        retries = min(max(int(retries_raw), 0), 5)
    except ValueError as exc:
        raise WarehouseNotificationError("QUANTHUS_RESEND_RETRIES must be an integer") from exc
    try:
        timeout = float(os.environ.get("QUANTHUS_RESEND_TIMEOUT_SECONDS", "15"))
    except ValueError as exc:
        raise WarehouseNotificationError("QUANTHUS_RESEND_TIMEOUT_SECONDS must be numeric") from exc
    body = _canonical_bytes(
        {
            "from": sender,
            "to": list(_recipients()),
            "subject": f"[Quanthus] {market_date.isoformat()} {EVENTS[event]['subject']}",
            "html": _email_html(
                event=event, market_date=market_date, pointer=pointer, result=result
            ),
        }
    )
    attempts = 0
    for attempts in range(1, retries + 2):
        try:
            http_request = request.Request(
                api_url,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": key,
                    "User-Agent": "Quanthus-Warehouse-Notifier/1.0",
                },
                method="POST",
            )
            with request.urlopen(http_request, timeout=timeout) as response:
                status = response.getcode()
                response_bytes = response.read()
            if not isinstance(status, int) or not 200 <= status < 300:
                raise WarehouseNotificationError(f"Resend returned HTTP status {status}")
            response_value: object = {}
            if response_bytes:
                try:
                    response_value = json.loads(response_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response_value = {}
            message_id = response_value.get("id") if isinstance(response_value, Mapping) else None
            return {
                "provider": "resend",
                "status": "sent",
                "attempts": attempts,
                "message_id": message_id if isinstance(message_id, str) else None,
            }
        except Exception as exc:
            if attempts > retries:
                if isinstance(exc, WarehouseNotificationError):
                    raise
                raise WarehouseNotificationError(f"Resend request failed: {exc}") from exc
            time.sleep(min(float(2 ** (attempts - 1)), 5.0))
    raise AssertionError("unreachable notification state")


def send_daily_event(
    *, event: str, daily_root: Path, market_date: date, state_root: Path
) -> dict[str, object]:
    if event not in EVENTS:
        raise WarehouseNotificationError(f"unsupported warehouse event: {event}")
    pointer, result = _package_from_current(daily_root=daily_root, market_date=market_date)
    key_material = f"quanthus-warehouse-v1\x1f{event}\x1f{market_date}\x1f{result.manifest_sha256}"
    key = hashlib.sha256(key_material.encode("ascii")).hexdigest()
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / f"{key}.lock"
    state_path = state_root / f"{key}.json"
    with lock_path.open("a+", encoding="ascii") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        if state_path.exists():
            value = _object(state_path)
            value["status"] = "skipped"
            value["idempotency_key"] = key
            return value
        sent = _send_resend(
            event=event,
            market_date=market_date,
            pointer=pointer,
            result=result,
            key=key,
        )
        record: dict[str, object] = {
            "schema_version": 1,
            "kind": "quanthus_warehouse_notification",
            "event": event,
            "market_date": market_date.isoformat(),
            "package_id": result.package_id,
            "manifest_sha256": result.manifest_sha256,
            "content_digest": result.content_digest,
            "idempotency_key": key,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            **sent,
        }
        _atomic_json(state_path, record)
        return record


def _parse_market_date(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise WarehouseNotificationError("market date must use YYYY-MM-DD") from exc
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=sorted(EVENTS), required=True)
    parser.add_argument("--daily-root", type=Path, required=True)
    parser.add_argument("--market-date")
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(
            os.environ.get("QUANTHUS_NOTIFICATION_STATE_ROOT", "/var/lib/quanthus/notifications")
        ),
    )
    args = parser.parse_args(argv)
    try:
        result = send_daily_event(
            event=args.event,
            daily_root=args.daily_root,
            market_date=_parse_market_date(args.market_date),
            state_root=args.state_root,
        )
    except WarehouseNotificationError as exc:
        print(f"warehouse notification failed: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
