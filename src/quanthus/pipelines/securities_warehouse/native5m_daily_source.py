"""Replay CN01 supplier-native 5m originals for a Base 2.4 Daily dependency.

The native parser/grid are adopted from GitHub revision
f839af0033ab4aa8cbe6d9fb933f29db88792e91, p10_native_minute_delivery.py.
No acquisition, pointer publication or 1m dependency is imported.

No-trade bar semantics (#692, rule NO_TRADE_BAR_RULE): a supplier row whose
six exact values (open, high, low, close, volume, amount) are all zero is a
source-observed no-trade bar, e.g. an unmatched opening auction followed by no
trade in the first 5m window. Such rows are kept exactly as observed and count
toward the session grid; no price is fabricated or forward-filled. Every other
zero/negative price or unit pattern remains fail-closed.
"""

from __future__ import annotations

import gzip
import importlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .base22 import canonical, file_sha
from .base22_sources import _fingerprint, _read_json, _tree, physical_path

PROVIDER = "baostock-native-5m"
FIELDS = "date,time,code,open,high,low,close,volume,amount"
KIND = "p10_source_native_minute_delivery_v1"
NO_TRADE_BAR_RULE = "p10-native-5m-observed-all-zero-no-trade-bar-v1"


def is_no_trade_values(
    op: Decimal, hi: Decimal, lo: Decimal, cl: Decimal, volume: Decimal, amount: Decimal
) -> bool:
    """Only the exact all-zero pattern is a source-observed no-trade bar (#692)."""
    return op == hi == lo == cl == volume == amount == 0


def is_no_trade_bar(bar: Mapping[str, Decimal]) -> bool:
    """True when a parsed bar carries the exact all-zero no-trade observation."""
    return is_no_trade_values(
        bar["open"], bar["high"], bar["low"], bar["close"], bar["volume_shares"], bar["amount"]
    )


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("source value is not Decimal") from exc
    if not number.is_finite():
        raise ValueError("nonfinite source value")
    return number


def _digest(value: Any) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(canonical(value) + b"\n").hexdigest()


def m5_grid(day: str) -> list[str]:
    base = datetime.fromisoformat(day)
    return [
        (base.replace(hour=hour, minute=minute) + timedelta(minutes=5 * i)).strftime("%Y%m%d%H%M")
        for hour, minute in [(9, 35), (13, 5)]
        for i in range(24)
    ]


def native_m5(raw: bytes, code: str, day: str) -> dict[str, dict[str, Decimal]]:
    value = json.loads(raw)
    if value.get("provider") == "baostock+tencent-observed-closing-auction-5m":
        from .closing_auction_source import replay

        return replay(raw, code, day)
    vendor_code = ("sh." if code.startswith("6") else "sz.") + code
    if (
        value.get("provider") != PROVIDER
        or value.get("frequency") != "5"
        or value.get("adjustflag") != "3"
        or value.get("code") != vendor_code
        or value.get("market_date") != day
        or value.get("fields") != FIELDS.split(",")
        or not isinstance(value.get("rows"), list)
    ):
        raise ValueError("native unadjusted 5m provenance differs")
    permitted = set(m5_grid(day))
    bars = {}
    prior = ""
    for item in value["rows"]:
        if (
            not isinstance(item, list)
            or len(item) != 9
            or item[0] != day
            or item[2] != vendor_code
            or not all(isinstance(x, str) for x in item)
        ):
            raise ValueError("native 5m row identity or exact strings differ")
        ts = datetime.strptime(item[1], "%Y%m%d%H%M%S%f")
        stamp = ts.strftime("%Y%m%d%H%M")
        if ts.second or ts.microsecond or stamp not in permitted or stamp <= prior:
            raise ValueError("duplicated/unordered/out-of-session native 5m")
        prior = stamp
        op, hi, lo, cl, volume, amount = [_decimal(x) for x in item[3:]]
        if not is_no_trade_values(op, hi, lo, cl, volume, amount) and (
            min(op, hi, lo, cl) <= 0
            or hi < max(op, lo, cl)
            or lo > min(op, hi, cl)
            or volume < 0
            or amount < 0
        ):
            raise ValueError("native 5m OHLC or exact units invalid")
        bars[stamp] = {
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume_shares": volume,
            "amount": amount,
        }
    return bars


def minute_schema() -> Any:
    pa = importlib.import_module("pyarrow")
    return pa.schema(
        [
            (n, pa.string())
            for n in ("stock_code", "market_date", "source_ts", "exchange_session_phase")
        ]
        + [
            (n, pa.decimal128(38, 10))
            for n in ("open", "high", "low", "close", "volume_shares", "amount")
        ]
        + [(n, pa.string()) for n in ("raw_sha256", "quality_grade", "quality_reason")]
        + [("original_source_observed", pa.bool_()), ("default_research_admitted", pa.bool_())]
    )


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("NATIVE5M_KNOWLEDGE_EVIDENCE_INVALID")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("NATIVE5M_KNOWLEDGE_EVIDENCE_INVALID") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("NATIVE5M_KNOWLEDGE_EVIDENCE_INVALID")
    return instant


def verify_native5m_source(
    root: Path,
    expected_manifest_sha256: str,
    *,
    day: str,
    daily: Mapping[str, Mapping[str, Any]],
    daily_sources: Mapping[str, str],
) -> dict[str, Any]:
    """Fully replay one existing native product against the exact new Daily inputs.

    The old research-ready flag describes its source only. This function admits
    no research or trading. Provider acquisition is not inferred from receipt or
    package creation times. At most one security's raw bars are held at a time.
    """
    root = physical_path(root)
    manifest, actual_manifest_sha, manifest_fingerprint = _read_json(root / "manifest.json")
    if actual_manifest_sha != expected_manifest_sha256:
        raise ValueError("NATIVE5M_MANIFEST_CHANGED")
    ready_fingerprint = _fingerprint(root / "READY")
    if (
        (root / "READY").read_text().strip() != expected_manifest_sha256
        or manifest.get("kind") != KIND
        or manifest.get("interval") != "5m"
        or manifest.get("market_date") != day
        or manifest.get("execution_authorized") is not False
        or manifest.get("policy_id") != "p10-trading-day-complete-daily-v1"
    ):
        raise ValueError("NATIVE5M_SOURCE_SCOPE_INVALID")
    fingerprints = {
        root / "manifest.json": manifest_fingerprint,
        root / "READY": ready_fingerprint,
    }
    declared = set()
    files = manifest.get("files", [])
    for entry in files:
        relative = entry["path"]
        p = Path(relative)
        if (
            p.is_absolute()
            or ".." in p.parts
            or p.as_posix() != relative
            or relative in declared
            or relative in {"manifest.json", "READY"}
        ):
            raise ValueError("NATIVE5M_MEMBER_PATH_INVALID")
        p = physical_path(root / p)
        st = p.stat()
        if (
            not p.is_file()
            or st.st_nlink != 1
            or st.st_size != entry["bytes"]
            or file_sha(p) != entry["sha256"]
        ):
            raise ValueError("NATIVE5M_MEMBER_CHANGED")
        fingerprints[p] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        declared.add(relative)
    if _tree(root) != declared | {"manifest.json", "READY"} or manifest.get(
        "content_digest"
    ) != _digest(files):
        raise ValueError("NATIVE5M_PHYSICAL_INVENTORY_INVALID")
    ref = json.loads((root / "daily-reference.json").read_bytes())
    ref_body = {k: v for k, v in ref.items() if k != "reference_digest"}
    securities = ref.get("securities", {})
    if (
        ref.get("kind") != "p10_complete_daily_job_reference_v3"
        or ref.get("market_date") != day
        or ref.get("reference_digest") != _digest(ref_body)
        or manifest.get("reference_digest") != ref.get("reference_digest")
        or ref.get("source_ods_manifest_sha256") != daily_sources.get("market_daily_core")
        or manifest.get("source_ods_manifest_sha256") != daily_sources.get("market_daily_core")
        or ref.get("event_ods_manifest_sha256") != daily_sources.get("calendar_daily_events")
        or set(securities) != set(daily)
        or ref.get("population") != len(daily)
        or ref.get("universe_policy_id") != "p10-acquisition-prevention-v1"
        or ref.get("universe_digest") != _digest(sorted(daily))
    ):
        raise ValueError("NATIVE5M_DAILY_DEPENDENCY_DIFFERS")
    statuses = {}
    sealed = _instant(manifest.get("sealed_at"))
    for line in (root / "quality-status.jsonl").read_text().splitlines():
        status = json.loads(line)
        code = status.get("stock_code")
        if (
            code not in daily
            or code in statuses
            or status.get("market_date") != day
            or status.get("reference_ods_manifest_sha256") != ref["source_ods_manifest_sha256"]
        ):
            raise ValueError("NATIVE5M_QUALITY_POPULATION_INVALID")
        grade = status.get("quality_grade")
        d = daily[code]
        prior = securities[code]
        if grade == "SUSPENDED":
            if prior.get("tradestatus") != "0" or d.get("is_tradable") is not False:
                raise ValueError("NATIVE5M_SUSPENSION_CONFLICT")
            if d.get("daily_price_observation_state") == "unavailable_suspended":
                if any(
                    prior.get(k) is not None or d.get(k) is not None for k in ["close", "volume"]
                ):
                    raise ValueError("NATIVE5M_SUSPENDED_PRICE_MUST_STAY_UNKNOWN")
            elif _decimal(d.get("volume")) != 0 or _decimal(prior.get("volume")) != 0:
                raise ValueError("NATIVE5M_SUSPENSION_CONFLICT")
            raw_path = root / "ods/raw" / (code + ".json.gz")
            if raw_path.exists():
                bars = native_m5(gzip.decompress(raw_path.read_bytes()), code, day)
                if any(x["volume_shares"] > 0 for x in bars.values()):
                    raise ValueError("NATIVE5M_SUSPENSION_CONFLICT")
        elif grade in {"R0", "R2"}:
            observed = _instant(status.get("source_acquired_at_or_after"))
            if observed > sealed or observed.date().isoformat() < day:
                raise ValueError("NATIVE5M_KNOWLEDGE_EVIDENCE_INVALID")
            if (
                prior.get("tradestatus") != "1"
                or d.get("is_tradable") is not True
                or _decimal(d.get("close")) != _decimal(prior.get("close"))
                or _decimal(d.get("volume")) != _decimal(prior.get("volume"))
                or status.get("formally_ingested") is not True
            ):
                raise ValueError("NATIVE5M_DAILY_DEPENDENCY_DIFFERS")
        else:
            raise ValueError("NATIVE5M_UNQUALIFIED_SOURCE")
        statuses[code] = status
    if set(statuses) != set(daily):
        raise ValueError("NATIVE5M_QUALITY_POPULATION_INVALID")
    if "processing_revision" in manifest:
        from .daily_base24_native_rebind import verify_binding

        verify_binding(root, ref, statuses)
    from .closing_auction_source import phase as source_phase
    from .closing_auction_source import verify_revision

    verify_revision(root, manifest, statuses)

    pq = importlib.import_module("pyarrow.parquet")
    pf = pq.ParquetFile(root / "minute-real-observed.parquet")
    if pf.schema_arrow != minute_schema():
        raise ValueError("NATIVE5M_PHYSICAL_SCHEMA_DIFFERS")
    visited: set[str] = set()
    current = None
    remaining: dict[str, dict[str, Decimal]] = {}
    counts: Counter[str] = Counter()
    for batch in pf.iter_batches(batch_size=8192, use_threads=False):
        for row in batch.to_pylist():
            code = row["stock_code"]
            if code != current:
                if (
                    remaining
                    or code in visited
                    or code not in statuses
                    or statuses[code]["quality_grade"] not in {"R0", "R2"}
                ):
                    raise ValueError("NATIVE5M_PHYSICAL_KEY_ORDER_OR_COVERAGE_INVALID")
                visited.add(code)
                current = code
                raw = gzip.decompress((root / "ods/raw" / (code + ".json.gz")).read_bytes())
                status = statuses[code]
                import hashlib

                if "sha256:" + hashlib.sha256(raw).hexdigest() != status.get("raw_sha256"):
                    raise ValueError("NATIVE5M_ORIGINAL_HASH_DIFFERS")
                if source_phase(
                    raw, day.replace("-", "") + "1500"
                ) != "continuous" and code not in manifest.get("closing_auction_revision", {}).get(
                    "codes", []
                ):
                    raise ValueError("AUCTION_UNDECLARED_COMPOUND_SOURCE")
                remaining = native_m5(raw, code, day)
                grid = m5_grid(day)
                if grid[-1] not in remaining or remaining[grid[-1]]["close"].quantize(
                    Decimal(".01")
                ) != _decimal(daily[code]["close"]).quantize(Decimal(".01")):
                    raise ValueError("NATIVE5M_TERMINAL_CLOSE_DIFFERS")
                if sum((x["volume_shares"] for x in remaining.values()), Decimal(0)) != _decimal(
                    daily[code]["volume"]
                ):
                    raise ValueError("NATIVE5M_DAILY_VOLUME_DIFFERS")
                grade = "R0" if len(remaining) == 48 else "R2"
                if status["quality_grade"] != grade:
                    raise ValueError("NATIVE5M_GRADE_DIFFERS_FROM_RAW")
            instant = datetime.fromisoformat(row["source_ts"])
            if instant.second or instant.microsecond or instant.tzinfo is not None:
                raise ValueError("NATIVE5M_PHYSICAL_TIME_DIFFERS_FROM_RAW")
            stamp = instant.strftime("%Y%m%d%H%M")
            original = remaining.pop(stamp, None)
            if (
                original is None
                or row["market_date"] != day
                or row["quality_grade"] != statuses[code]["quality_grade"]
                or row["quality_reason"] != statuses[code]["quality_reason"]
                or row["raw_sha256"] != statuses[code]["raw_sha256"]
                or row["original_source_observed"] is not True
                or row["default_research_admitted"] is not True
                or row["exchange_session_phase"] != source_phase(raw, stamp)
                or any(
                    row[name] != original[name]
                    for name in ("open", "high", "low", "close", "volume_shares", "amount")
                )
            ):
                raise ValueError("NATIVE5M_PHYSICAL_VALUES_OR_TIME_DIFFER_FROM_RAW")
            counts[row["quality_grade"]] += 1
    grades = dict(Counter(x["quality_grade"] for x in statuses.values()))
    if (
        remaining
        or visited != {c for c, s in statuses.items() if s["quality_grade"] in {"R0", "R2"}}
        or manifest.get("grades") != grades
        or manifest.get("rows_per_grade") != dict(counts)
    ):
        raise ValueError("NATIVE5M_COMPLETE_CENSUS_DIFFERS")
    for p, before in fingerprints.items():
        s = p.stat()
        if (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != before:
            raise ValueError("NATIVE5M_SOURCE_CHANGED_DURING_REPLAY")
    return {
        "status": "PASS_SOURCE_REPLAY_NOT_DAILY_COMPOSITE",
        "manifest_sha256": expected_manifest_sha256,
        "day": day,
        "population": len(statuses),
        "grades": grades,
        "rows": sum(counts.values()),
        "rows_per_grade": dict(counts),
        "provider_acquisition": "NOT_INFERRED",
        "research_admission": "NOT_GRANTED",
        "execution_authorized": False,
    }
