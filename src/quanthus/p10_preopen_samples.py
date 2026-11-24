"""Finite #727 raw-field sampling on the existing Tencent endpoint.

This records evidence for later field qualification. It supplies no advice,
orders, quote qualification, or implied auction/suspension facts.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import date, datetime, timezone
from datetime import time as wall_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
DATES = {"2026-11-25", "2026-11-26"}
SLOTS = ("09:15:05", "09:20:05", "09:24:50", "09:25:10", "09:29:50")
EXCLUDED = {
    "300308",
    "300502",
    "300394",
    "002463",
    "300476",
    "601138",
    "000977",
    "002916",
    "002436",
    "002837",
    "688256",
    "688041",
    "399006",
    "000300",
}
URL = "https://qt.gtimg.cn/q="
LAYOUT_SOURCE = "adda13da6c6d20382164f4c3dd0b8c286994bb78:src/quanthus/p12_paper/quotes.py"


class SamplingError(ValueError):
    """Stable finite-sampling boundary failure."""


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def now() -> datetime:
    return datetime.now(timezone.utc)


def instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SamplingError("INVALID_CLOCK")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError) as exc:
        raise SamplingError("INVALID_CLOCK") from exc
    if result.tzinfo is None:
        raise SamplingError("TIMEZONE_REQUIRED")
    return result


def boundary(day: str, value: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), wall_time.fromisoformat(value), SHANGHAI)


def exclusive(path: Path, body: bytes) -> None:
    """Exclusive immutable evidence; callers reserve a request before transport."""
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def freeze_pool(raw: Mapping[str, Any], frozen_at: datetime) -> dict[str, Any]:
    day = raw.get("sample_date")
    if day not in DATES or frozen_at.tzinfo is None:
        raise SamplingError("DATE_OUTSIDE_FINITE_PLAN")
    if frozen_at >= boundary(day, "09:15:00"):
        raise SamplingError("POOL_FREEZE_TOO_LATE")
    reference_day = raw.get("reference_trade_date")
    if reference_day != {"2026-11-25": "2026-11-24", "2026-11-26": "2026-11-25"}[day]:
        raise SamplingError("T_REFERENCE_DATE_MISMATCH")
    items = raw.get("securities")
    if not isinstance(items, list) or not 2 <= len(items) <= 18:
        raise SamplingError("POOL_SIZE_INVALID")
    symbols: set[str] = set()
    codes: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise SamplingError("POOL_SECURITY_IDENTITY_INVALID")
        code, symbol = item.get("security_code"), item.get("tencent_symbol")
        if (
            not isinstance(code, str)
            or re.fullmatch(r"\d{6}", code) is None
            or code in EXCLUDED
            or code in codes
            or item.get("mic_code") not in {"XSHG", "XSHE", "BJSE"}
            or symbol
            != {"XSHG": "sh", "XSHE": "sz", "BJSE": "bj"}.get(str(item.get("mic_code")), "") + code
            or symbol in symbols
        ):
            raise SamplingError("POOL_SECURITY_IDENTITY_INVALID")
        refs = item.get("reference_evidence")
        if not isinstance(refs, list) or not refs:
            raise SamplingError("POOL_REFERENCE_REQUIRED")
        for ref in refs:
            if (
                not isinstance(ref, dict)
                or not isinstance(ref.get("uri"), str)
                or not ref["uri"]
                or re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256"))) is None
                or instant(ref.get("observed_at")) > frozen_at
            ):
                raise SamplingError("POOL_REFERENCE_IDENTITY_OR_CLOCK")
        codes.add(code)
        symbols.add(symbol)
    if not {"002517", "600000"} <= codes:
        raise SamplingError("FALLBACK_HOLDINGS_MISSING")
    if (
        raw.get("fallback_sha256")
        != "4131dac0b0462203391826e5fb1c259c42218746668224981ed46fec7216d56c"
    ):
        raise SamplingError("FALLBACK_IDENTITY_MISMATCH")
    semantic = {k: v for k, v in raw.items() if k != "identity_sha256"}
    result = {
        **semantic,
        "schema": "quanthus.p10.preopen-frozen-pool.v1",
        "frozen_at": frozen_at.isoformat(),
        "qualification": "RAW_FIELD_VALIDATION_ONLY",
        "slots": list(SLOTS),
    }
    result["identity_sha256"] = sha(encoded(result))
    return result


def verify_pool(pool: dict[str, Any]) -> None:
    original = {k: v for k, v in pool.items() if k != "identity_sha256"}
    if pool.get("identity_sha256") != sha(encoded(original)):
        raise SamplingError("FROZEN_POOL_SHA_MISMATCH")
    rebuilt = freeze_pool(pool, instant(pool["frozen_at"]))
    # freeze_pool is also used for strict replay, without moving the original clock.
    rebuilt.pop("identity_sha256")
    if rebuilt != original:
        raise SamplingError("FROZEN_POOL_CONTENT_MISMATCH")


def persist_pool(pool: dict[str, Any], root: Path, recorded_at: datetime) -> None:
    """Durably freeze before the cutoff; resume only a matching completed freeze."""
    verify_pool(pool)
    root.mkdir(parents=True, exist_ok=True)
    binding, receipt = root / "POOL.json", root / "FREEZE.json"
    if binding.exists() and binding.read_bytes() != encoded(pool):
        raise SamplingError("DAY_POOL_REBOUND")
    if receipt.exists():
        saved = json.loads(receipt.read_bytes())
        if (
            not binding.exists()
            or saved.get("pool_file_sha256") != sha(encoded(pool))
            or saved.get("pool_identity_sha256") != pool["identity_sha256"]
            or not instant(pool["frozen_at"]) <= instant(saved["recorded_at"]) <= recorded_at
            or instant(saved["recorded_at"]) >= boundary(pool["sample_date"], "09:15:00")
        ):
            raise SamplingError("FREEZE_RECEIPT_INVALID")
        return
    if recorded_at >= boundary(pool["sample_date"], "09:15:00") or recorded_at < instant(
        pool["frozen_at"]
    ):
        raise SamplingError("DURABLE_FREEZE_TOO_LATE_OR_FUTURE")
    if any(p.name not in {".lock", "POOL.json"} for p in root.iterdir()):
        raise SamplingError("DEDICATED_EVIDENCE_ROOT_REQUIRED")
    if not binding.exists():
        exclusive(binding, encoded(pool))
    exclusive(
        receipt,
        encoded(
            {
                "schema": "quanthus.p10.preopen-freeze-receipt.v1",
                "sample_date": pool["sample_date"],
                "recorded_at": recorded_at.isoformat(),
                "pool_identity_sha256": pool["identity_sha256"],
                "pool_file_sha256": sha(encoded(pool)),
            }
        ),
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow this endpoint to another source or host."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise SamplingError("SOURCE_REDIRECT_REJECTED")


def source_open(req: urllib.request.Request, *, timeout: int) -> Any:
    return urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout)


def raw_fields(body: bytes, symbols: list[str]) -> list[dict[str, Any]]:
    text = body.decode("gb18030", errors="replace")
    rows: dict[str, list[str]] = {}
    for symbol, payload in re.findall(r'v_([a-z]{2}\d{6})="([^"\r\n]*)";', text):
        if symbol in rows or symbol not in symbols:
            raise SamplingError("RESPONSE_SECURITY_IDENTITY_MISMATCH")
        rows[symbol] = payload.split("~")
    result: list[dict[str, Any]] = []
    for symbol in symbols:
        fields = rows.get(symbol)
        if fields is None:
            result.append({"symbol": symbol, "status": "unavailable", "reason": "MEMBER_MISSING"})
            continue
        if len(fields) <= 34 or fields[2] != symbol[2:]:
            raise SamplingError("RESPONSE_SECURITY_IDENTITY_MISMATCH")
        source_clock = None
        with suppress(ValueError):
            source_clock = (
                datetime.strptime(fields[30], "%Y%m%d%H%M%S")
                .replace(
                    tzinfo=SHANGHAI,
                )
                .isoformat()
            )
        result.append(
            {
                "symbol": symbol,
                "status": "RAW_CAPTURED",
                "fields": fields,
                "quote_time_raw": fields[30],
                "quote_time": source_clock,
                "quote_time_unknown_reason": None if source_clock else "UNPARSEABLE_SOURCE_CLOCK",
                "layout_reference": LAYOUT_SOURCE,
                "last_field": 3,
                "open_field": 5,
                "auction_field": None,
                "limit_fields": None,
                "suspension_field": None,
                "unverified_fields": "All raw positions retained for #727 qualification",
            }
        )
    return result


def capture_slot(
    pool: dict[str, Any],
    root: Path,
    slot: str,
    *,
    clock: Callable[[], datetime] = now,
    opener: Callable[..., Any] = source_open,
) -> dict[str, Any]:
    verify_pool(pool)
    day = pool["sample_date"]
    started = clock()
    if slot not in SLOTS or started.astimezone(SHANGHAI).date().isoformat() != day:
        raise SamplingError("REQUEST_DATE_OR_SLOT_INVALID")
    target = boundary(day, slot)
    if not 0 <= (started - target).total_seconds() <= 5:
        raise SamplingError("SLOT_NOT_CURRENT")
    persist_pool(pool, root, started)
    if (root / "HTTP-STOP.json").exists():
        raise SamplingError("SOURCE_HELD_NO_MORE_REQUESTS")
    if len(list(root.glob("*.request.json"))) >= 6:
        raise SamplingError("DAY_REQUEST_BUDGET")
    identity = pool["identity_sha256"]
    key = slot.replace(":", "")
    symbols = sorted(item["tencent_symbol"] for item in pool["securities"])
    request = {
        "sample_date": day,
        "slot": slot,
        "started_at": started.isoformat(),
        "pool_sha256": identity,
        "url": URL + ",".join(symbols),
    }
    try:
        exclusive(root / (key + ".request.json"), encoded(request))
    except FileExistsError as exc:
        raise SamplingError("SLOT_ALREADY_RESERVED_NO_RETRY") from exc
    record: dict[str, Any] = {
        **request,
        "source": "tencent:qt.gtimg.cn",
        "qualification": "RAW_FIELD_VALIDATION_ONLY",
    }
    try:
        req = urllib.request.Request(
            request["url"], headers={"User-Agent": "Mozilla/5.0 Quanthus-P10/727"}
        )
        with opener(req, timeout=8) as response:
            body = response.read(256 * 1024 + 1)
            record["http_status"] = response.status
        received = clock()
        record["received_at"] = received.isoformat()
        if len(body) > 256 * 1024:
            raise SamplingError("RESPONSE_SIZE_BUDGET")
        exclusive(root / (key + ".wire"), body)
        record.update(raw_sha256=sha(body), raw_size_bytes=len(body))
        record["rows"] = raw_fields(body, symbols)
        if received < started:
            raise SamplingError("RECEIPT_CLOCK_REGRESSION")
        record["status"] = (
            "CAPTURED" if received < boundary(day, "09:30:00") else "LATE_RECEIPT_UNQUALIFIED"
        )
    except urllib.error.HTTPError as exc:
        record.update(status="unavailable", http_status=exc.code, reason="SOURCE_HTTP_ERROR")
        if exc.code in {401, 403, 429}:
            exclusive(root / "HTTP-STOP.json", encoded(record))
    except Exception as exc:
        record.update(status="unavailable", reason=type(exc).__name__ + ":" + str(exc))
    exclusive(root / (key + ".receipt.json"), encoded(record))
    return record


def run_pool(
    pool: dict[str, Any],
    root: Path,
    *,
    clock: Callable[[], datetime] = now,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = source_open,
) -> None:
    verify_pool(pool)
    start = clock()
    day = pool["sample_date"]
    if not boundary(day, "08:55:00") <= start < boundary(day, "09:30:00"):
        raise SamplingError("RUN_OUTSIDE_NAMED_WINDOW")
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        persist_pool(pool, root, clock())
        for slot in SLOTS:
            target = boundary(day, slot)
            while (remaining := (target - clock()).total_seconds()) > 0:
                if not boundary(day, "08:55:00") <= clock() < boundary(day, "09:30:00"):
                    raise SamplingError("RUN_CLOCK_OUTSIDE_WINDOW")
                sleeper(min(30, remaining))
            try:
                capture_slot(pool, root, slot, clock=clock, opener=opener)
            except SamplingError as exc:
                skipped = {
                    "slot": slot,
                    "status": "unavailable",
                    "reason": str(exc),
                    "recorded_at": clock().isoformat(),
                    "pool_sha256": pool["identity_sha256"],
                }
                path = root / (slot.replace(":", "") + ".skipped.json")
                if not path.exists():
                    exclusive(path, encoded(skipped))
                print(json.dumps(skipped))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "prepare-run"))
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pool = json.loads(args.pool.read_text())
    if args.action == "prepare-run":
        from quanthus.p10_preopen_pool import prepare

        started = now()
        day = started.astimezone(SHANGHAI).date().isoformat()
        if not boundary(day, "08:55:00") <= started < boundary(day, "09:15:00"):
            raise SamplingError("PREPARE_OUTSIDE_NAMED_WINDOW")
        pool = freeze_pool(prepare(pool, day, started), now())
    if args.action == "freeze":
        frozen = freeze_pool(pool, now())
        persist_pool(frozen, args.output, now())
        print(json.dumps({"frozen_pool": str(args.output), "sha256": sha(encoded(frozen))}))
        return
    run_pool(pool, args.output)


if __name__ == "__main__":
    main()
