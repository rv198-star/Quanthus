"""Idempotent operational email notifications for the DWD daily handoff."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from urllib import error as urllib_error
from urllib import request
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse import (
    DailyDomainBuildError,
    DailyDomainResult,
    verify_daily_domain_increment,
)


class WarehouseNotificationError(RuntimeError):
    """Raised when a warehouse completion notification cannot be delivered."""

    def __init__(
        self, message: str, *, code: str = "notification_failed", attempts: int = 0
    ) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts


EVENTS = {
    "cn01_dwd_completed": {
        "node": "CN01",
        "stage": "DWD构建",
        "heading": "CN01 DWD 构建完成",
        "failure": False,
    },
    "cn01_dwd_failed": {
        "node": "CN01",
        "stage": "DWD构建",
        "heading": "CN01 DWD 构建失败",
        "failure": True,
    },
    "hermes_dwd_completed": {
        "node": "Hermes",
        "stage": "DWD构建",
        "heading": "Hermes DWD 构建完成",
        "failure": False,
    },
    "hermes_dwd_failed": {
        "node": "Hermes",
        "stage": "DWD构建",
        "heading": "Hermes DWD 构建失败",
        "failure": True,
    },
    "oci_dwd_sync_completed": {
        "node": "OCI",
        "stage": "DWD同步",
        "heading": "OCI DWD 增量同步完成",
        "failure": False,
    },
    "oci_dwd_sync_failed": {
        "node": "OCI",
        "stage": "DWD同步",
        "heading": "OCI DWD 增量同步失败",
        "failure": True,
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


def _bounded_error(value: object) -> str:
    """Keep provider and system errors useful without persisting unbounded text or secrets."""

    if value is None:
        return "unspecified failure"
    text = " ".join(str(value).split())
    api_key = os.environ.get("QUANTHUS_RESEND_API_KEY", "").strip()
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return text[:2000] or "unspecified failure"


def _failure_digest(value: object) -> str:
    # Systemd paths and timestamps often vary between retries; do not make every retry a new
    # logical email just because those volatile fragments changed.
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}T[^\s]+", "<timestamp>", _bounded_error(value))
    normalized = re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", normalized, flags=re.IGNORECASE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _package_from_current(
    *, daily_root: Path, market_date: date
) -> tuple[dict[str, object], DailyDomainResult]:
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
    *,
    event: str,
    market_date: date,
    pointer: Mapping[str, object] | None,
    result: DailyDomainResult | None,
    error: str | None,
    context: Mapping[str, object],
) -> str:
    node = str(context.get("node") or EVENTS[event]["node"])
    stage = str(context.get("stage") or EVENTS[event]["stage"])
    details: list[tuple[str, str]] = [
        ("节点", node),
        ("阶段", stage),
        ("交易日", market_date.isoformat()),
        ("状态", "失败" if EVENTS[event]["failure"] else "成功"),
    ]
    if result is not None:
        details.extend(
            [
                ("包 ID", str(result.package_id)),
                ("manifest SHA-256", str(result.manifest_sha256)),
                ("content digest", str(result.content_digest)),
                ("载荷大小", f"{result.payload_bytes} bytes"),
                ("行数", json.dumps(dict(result.row_counts), ensure_ascii=False, sort_keys=True)),
                ("CURRENT", str(pointer.get("manifest_path", "-")) if pointer else "-"),
            ]
        )
    if error:
        details.append(("错误", error))
    context_labels = {
        "unit": "systemd unit",
        "run_path": "运行路径",
        "error_code": "错误分类",
    }
    for key, label in context_labels.items():
        value = context.get(key)
        if value is not None and str(value).strip():
            details.append((label, str(value)))
    rows = "".join(
        f'<tr><th align="left">{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>'
        for label, value in details
    )
    return (
        "<!doctype html><html><body>"
        f"<h2>{html.escape(str(EVENTS[event]['heading']))}</h2>"
        f'<table border="1" cellpadding="6" cellspacing="0">{rows}</table>'
        "</body></html>"
    )


def _send_resend(
    *,
    event: str,
    market_date: date,
    pointer: Mapping[str, object] | None,
    result: DailyDomainResult | None,
    key: str,
    error: str | None = None,
    context: Mapping[str, object] | None = None,
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
            "subject": (
                f"[Quanthus][{EVENTS[event]['node']}][{EVENTS[event]['stage']}] "
                f"{market_date.isoformat()} "
                f"{'失败' if EVENTS[event]['failure'] else '成功'}"
            ),
            "html": _email_html(
                event=event,
                market_date=market_date,
                pointer=pointer,
                result=result,
                error=error,
                context=context or {},
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
            try:
                with request.urlopen(http_request, timeout=timeout) as response:
                    status = response.getcode()
                    response_bytes = response.read()
            except urllib_error.HTTPError as exc:
                response_bytes = exc.read(1024)
                detail = response_bytes.decode("utf-8", errors="replace").strip()
                if detail:
                    detail = f": {_bounded_error(detail)}"
                raise WarehouseNotificationError(
                    f"Resend returned HTTP status {exc.code}{detail}",
                    code="resend_http_error",
                ) from exc
            if not isinstance(status, int) or not 200 <= status < 300:
                raise WarehouseNotificationError(
                    f"Resend returned HTTP status {status}", code="resend_http_error"
                )
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
                message = _bounded_error(exc)
                if isinstance(exc, WarehouseNotificationError):
                    raise WarehouseNotificationError(
                        message, code=exc.code, attempts=attempts
                    ) from exc
                raise WarehouseNotificationError(
                    f"Resend request failed: {message}",
                    code="resend_request_failed",
                    attempts=attempts,
                ) from exc
            time.sleep(min(float(2 ** (attempts - 1)), 5.0))
    raise AssertionError("unreachable notification state")


def _failure_context(
    *, failure_receipt: Path | None, error: str | None, context: Mapping[str, object] | None
) -> tuple[str, dict[str, object]]:
    values = dict(context or {})
    if failure_receipt is not None:
        receipt = _object(failure_receipt)
        for key in ("error", "error_code", "stage", "node", "unit", "run_path", "market_date"):
            if key in receipt and key not in values:
                values[key] = receipt[key]
    raw_error = error if error is not None else values.get("error")
    bounded = _bounded_error(raw_error)
    values["error"] = bounded
    return bounded, values


def send_daily_event(
    *,
    event: str,
    daily_root: Path,
    market_date: date | None,
    state_root: Path,
    error: str | None = None,
    context: Mapping[str, object] | None = None,
    failure_receipt: Path | None = None,
) -> dict[str, object]:
    if event not in EVENTS:
        raise WarehouseNotificationError(f"unsupported warehouse event: {event}")
    failure = bool(EVENTS[event]["failure"])
    event_context: dict[str, object] = dict(context or {})
    if failure:
        failure_error, event_context = _failure_context(
            failure_receipt=failure_receipt, error=error, context=event_context
        )
        if market_date is None:
            raw_date = event_context.get("market_date")
            if isinstance(raw_date, str):
                try:
                    market_date = date.fromisoformat(raw_date)
                except ValueError as exc:
                    raise WarehouseNotificationError(
                        "failure receipt market_date is invalid"
                    ) from exc
        if market_date is None:
            market_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        pointer = None
        result = None
        key_material = (
            f"quanthus-warehouse-v1\x1f{event}\x1f{market_date}\x1f"
            f"{event_context.get('stage', '')}\x1f{event_context.get('run_path', '')}\x1f"
            f"{_failure_digest(failure_error)}"
        )
    else:
        if market_date is None:
            market_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        pointer, result = _package_from_current(daily_root=daily_root, market_date=market_date)
        failure_error = None
        key_material = (
            f"quanthus-warehouse-v1\x1f{event}\x1f{market_date}\x1f{result.manifest_sha256}"
        )
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
        try:
            sent = _send_resend(
                event=event,
                market_date=market_date,
                pointer=pointer,
                result=result,
                key=key,
                error=failure_error,
                context=event_context,
            )
        except Exception as exc:
            notification_error = (
                exc
                if isinstance(exc, WarehouseNotificationError)
                else WarehouseNotificationError(
                    f"notification delivery failed: {_bounded_error(exc)}"
                )
            )
            failure_record: dict[str, object] = {
                "schema_version": 1,
                "kind": "quanthus_warehouse_notification_failure",
                "event": event,
                "market_date": market_date.isoformat(),
                "idempotency_key": key,
                "delivery_status": "failed",
                "error_code": notification_error.code,
                "error": _bounded_error(notification_error),
                "attempts": notification_error.attempts,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_json(state_root / "failures" / f"{key}.json", failure_record)
            raise notification_error from exc
        record: dict[str, object] = {
            "schema_version": 1,
            "kind": "quanthus_warehouse_notification",
            "event": event,
            "market_date": market_date.isoformat(),
            "idempotency_key": key,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            **sent,
        }
        if result is not None:
            record.update(
                {
                    "package_id": result.package_id,
                    "manifest_sha256": result.manifest_sha256,
                    "content_digest": result.content_digest,
                }
            )
        if event_context:
            record["context"] = event_context
        if failure_error is not None:
            record.update(
                {
                    "delivery_status": "sent",
                    "error": failure_error,
                }
            )
        _atomic_json(state_path, record)
        return record


def send_failure_events(
    *, failure_receipt: Path, daily_root: Path, state_root: Path
) -> dict[str, object]:
    """Send the OCI synchronization failure alert."""

    try:
        receipt = _object(failure_receipt)
    except WarehouseNotificationError as exc:
        # A full sync failure can include a disk error before its receipt is written.  Keep a
        # generic OCI alert possible in that case; the journal still contains the detailed cause.
        record = send_daily_event(
            event="oci_dwd_sync_failed",
            daily_root=daily_root,
            market_date=None,
            state_root=state_root,
            error=f"failure receipt unavailable: {exc}",
            context={
                "stage": "oci_dwd_sync",
                "node": os.environ.get("QUANTHUS_NODE", "OCI"),
                "unit": os.environ.get("QUANTHUS_SYSTEMD_UNIT", "oci-dwd-daily-sync.service"),
            },
        )
        return {"status": "sent", "events": [record]}
    failed_at = receipt.get("failed_at")
    if isinstance(failed_at, str):
        try:
            failure_time = datetime.fromisoformat(failed_at)
            if failure_time.tzinfo is None:
                raise ValueError("missing timezone")
        except ValueError as exc:
            raise WarehouseNotificationError("failure receipt failed_at is invalid") from exc
        max_age = int(os.environ.get("QUANTHUS_FAILURE_RECEIPT_MAX_AGE_SECONDS", "600"))
        age = (datetime.now(timezone.utc) - failure_time.astimezone(timezone.utc)).total_seconds()
        if age > max_age:
            return {"status": "skipped_stale", "events": [], "receipt_age_seconds": int(age)}
    record = send_daily_event(
        event="oci_dwd_sync_failed",
        daily_root=daily_root,
        market_date=None,
        state_root=state_root,
        failure_receipt=failure_receipt,
    )
    return {"status": "sent", "events": [record]}


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
    parser.add_argument("--error")
    parser.add_argument("--stage")
    parser.add_argument("--node")
    parser.add_argument("--unit")
    parser.add_argument("--run-path")
    parser.add_argument("--failure-receipt", type=Path)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(
            os.environ.get("QUANTHUS_NOTIFICATION_STATE_ROOT", "/var/lib/quanthus/notifications")
        ),
    )
    args = parser.parse_args(argv)
    context = {
        key: value
        for key, value in {
            "stage": args.stage,
            "node": args.node,
            "unit": args.unit,
            "run_path": args.run_path,
        }.items()
        if value
    }
    try:
        result = send_daily_event(
            event=args.event,
            daily_root=args.daily_root,
            market_date=_parse_market_date(args.market_date) if args.market_date else None,
            state_root=args.state_root,
            error=args.error,
            context=context,
            failure_receipt=args.failure_receipt,
        )
    except WarehouseNotificationError as exc:
        print(f"warehouse notification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
