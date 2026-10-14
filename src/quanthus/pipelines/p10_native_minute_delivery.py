"""Native 1m/5m products with a shared, sealed daily reference and real grades."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.pipelines.loss_aware_m1_research import _reference
from quanthus.pipelines.p10_complete_daily_schedule import CST, POLICY_ID
from quanthus.pipelines.p10_delivery_common import (
    atomic,
    digest,
    encoded,
    file_digest,
    object_at,
    require_revision,
    seal,
    verify_seal,
)
from quanthus.pipelines.p10_minute_live_warehouse import _decimal, assess_native
from quanthus.providers.p10_native_m1_shard_capture import capture_shard as capture_m1
from quanthus.providers.p10_native_m1_shard_capture import target_date_records
from quanthus.providers.p10_native_m5_shard_capture import FIELDS, PROVIDER
from quanthus.providers.p10_native_m5_shard_capture import capture_shard as capture_m5

KIND = "p10_source_native_minute_delivery_v1"
ALLOWED = {"R0", "R1", "R2"}


def publish_pointer(product_root: Path, result: dict[str, Any]) -> None:
    path = product_root / "date-pointers" / f"{result['market_date']}.json"
    if path.exists() and object_at(path).get("manifest_sha256") == result["manifest_sha256"]:
        return
    atomic(path, {**result, "first_verified_published_at": datetime.now(timezone.utc).isoformat()})


def freeze_reference(
    release: Path,
    day: str,
    destination: Path,
    *,
    events_sha: str | None = None,
) -> dict[str, Any]:
    """Materialize the same daily prerequisite once per Job, never scan Base."""
    import pyarrow.parquet as pq

    market, sha, statuses = _reference(release, day)
    manifest = object_at(release / "release.json")
    artifacts = {item["name"]: item for item in manifest["artifacts"]}
    if (
        os.environ.get("HERMES_ODS_PREVENTION_POLICY") == "p10-acquisition-prevention-v1"
        and "source_universe" not in artifacts
    ):
        raise ValueError("target daily prerequisite lacks refreshed acquisition universe")
    for name in ("daily_ohlcv_none_delta", "daily_status_observation_delta"):
        path = release / artifacts[name]["relative_path"]
        parquet = pq.ParquetFile(path)
        for key in ("market_date", "date"):
            if key not in parquet.schema_arrow.names or set(
                parquet.read(columns=[key]).column(key).to_pylist()
            ) != {day}:
                raise ValueError("daily reference physical rows have wrong target date")
        if name == "daily_ohlcv_none_delta" and (
            "adj_mode" not in parquet.schema_arrow.names
            or set(parquet.read(columns=["adj_mode"]).column("adj_mode").to_pylist()) != {"none"}
        ):
            raise ValueError("minute reference requires unadjusted daily observations")
    ref = {
        "kind": "p10_complete_daily_job_reference_v1",
        "market_date": day,
        "source_ods_manifest_sha256": sha,
        "event_ods_manifest_sha256": events_sha,
        "policy_id": POLICY_ID,
        "population": len(statuses),
        "securities": {
            code: {
                "close": str(market[code]["close"]) if code in market else None,
                "volume": str(market[code]["volume"]) if code in market else None,
                "tradestatus": statuses[code],
                **(
                    {"daily_price_observation_state": "unavailable_suspended"}
                    if code not in market
                    else {}
                ),
            }
            for code in sorted(statuses)
        },
    }
    if "source_universe" in artifacts:
        universe = object_at(release / artifacts["source_universe"]["relative_path"])
        ref["kind"] = "p10_complete_daily_job_reference_v2"
        ref["universe_digest"] = universe["universe_digest"]
        ref["universe_manifest_sha256"] = artifacts["source_universe"]["sha256"]
        ref["universe_policy_id"] = universe["policy_id"]
        if set(statuses) - set(market):
            ref["kind"] = "p10_complete_daily_job_reference_v3"
    ref["reference_digest"] = digest(encoded(ref))
    if destination.exists():
        if object_at(destination) != ref:
            raise ValueError("immutable Job reference changed")
    else:
        atomic(destination, ref)
    return ref


def reference(path: Path) -> dict[str, Any]:
    value = object_at(path)
    content = {key: item for key, item in value.items() if key != "reference_digest"}
    codes = value.get("securities", {})
    legacy = value.get("kind") == "p10_complete_daily_job_reference_v1"
    dynamic = value.get("kind") == "p10_complete_daily_job_reference_v2"
    suspended_reference = value.get("kind") == "p10_complete_daily_job_reference_v3"
    dynamic = dynamic or suspended_reference
    if dynamic and (
        value.get("universe_policy_id") != "p10-acquisition-prevention-v1"
        or value.get("universe_digest") != digest(encoded(sorted(codes)))
        or not str(value.get("universe_manifest_sha256", "")).startswith("sha256:")
    ):
        raise ValueError("versioned dynamic universe identity differs")
    if suspended_reference:
        from quanthus.providers.acquisition_evidence import decimal_token

        for row in codes.values():
            if row.get("daily_price_observation_state") == "unavailable_suspended":
                if (
                    row.get("tradestatus") != "0"
                    or row.get("close") is not None
                    or row.get("volume") is not None
                ):
                    raise ValueError("unavailable suspended price must remain null")
            else:
                decimal_token(row.get("close"), required=True)
                decimal_token(row.get("volume"), required=True)
    if (
        not (legacy or dynamic)
        or value.get("policy_id") != POLICY_ID
        or value.get("reference_digest") != digest(encoded(content))
        or (legacy and value.get("population") != 4992)
        or not codes
        or len(codes) != value.get("population")
        or any(len(code) != 6 or not code.isdigit() for code in codes)
        or any(row.get("tradestatus") not in {"0", "1"} for row in codes.values())
    ):
        raise ValueError("sealed daily reference identity/population differs")
    return value


def m5_grid(day: str) -> list[str]:
    base = datetime.fromisoformat(day)
    return [
        (base.replace(hour=hour, minute=minute) + timedelta(minutes=5 * i)).strftime("%Y%m%d%H%M")
        for hour, minute in [(9, 35), (13, 5)]
        for i in range(24)
    ]


def native_m5(raw: bytes, code: str, day: str) -> dict[str, dict[str, Decimal]]:
    value = json.loads(raw)
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
        if (
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


def target_source_changed(before: bytes, after: bytes, code: str, day: str, interval: str) -> bool:
    """Compare admissible target records; invalid originals remain quarantined.

    A valid revision can repair an invalid original. Invalid new records cannot
    replace valid old observations or authorize a speculative repair pass.
    """

    def records(raw: bytes) -> Any:
        if interval == "1m":
            return target_date_records(raw, code, date.fromisoformat(day))[0]
        if interval != "5m":
            raise ValueError("unsupported native interval")
        return native_m5(raw, code, day)

    try:
        new = records(after)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    if not new:
        return False
    try:
        old = records(before)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return True
    return bool(old != new)


def assess(
    *, interval: str, code: str, day: str, raw: bytes | None, daily: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Decimal]]]:
    if daily.get("daily_price_observation_state") == "unavailable_suspended":
        if (
            interval not in {"1m", "5m"}
            or daily["tradestatus"] != "0"
            or daily["volume"] is not None
            or daily["close"] is not None
        ):
            raise ValueError("unavailable price cannot justify an open security")
        quality = {
            "stock_code": code,
            "market_date": day,
            "quality_grade": "SUSPENDED",
            "quality_reason": "sealed_daily_suspension_price_unavailable",
            "formally_ingested": False,
            "default_research_admitted": False,
            "raw_sha256": digest(raw) if raw else None,
            "missing_actual_minutes": [],
            "auction_source_observed": False,
            "allowed_consumer": "none",
            "daily_share_delta": None,
            "daily_close_delta": None,
            "daily_price_observation_state": "unavailable_suspended",
        }
        if raw is not None:
            try:
                if interval == "1m":
                    observed, _ = target_date_records(raw, code, date.fromisoformat(day))
                    positive_volume = any(_decimal(row[5]) > 0 for row in observed)
                else:
                    positive_volume = any(
                        bar["volume_shares"] > 0 for bar in native_m5(raw, code, day).values()
                    )
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                quality["minute_source_consistency"] = "unavailable_or_invalid"
                quality["minute_source_detail"] = str(exc)
            else:
                quality["minute_source_consistency"] = (
                    "conflict" if positive_volume else "no_target_positive_volume"
                )
                if positive_volume:
                    quality["quality_grade"] = "R3"
                    quality["quality_reason"] = "suspended_with_positive_target_minute_volume"
        return quality, {}
    if interval == "1m":
        quality, bars = assess_native(
            code=code,
            day=day,
            raw=raw,
            traded=daily["tradestatus"] == "1",
            daily_shares=daily["volume"],
            daily_close=daily["close"],
        )
        if any(
            bar[name] <= 0 for bar in bars.values() for name in ("open", "high", "low", "close")
        ):
            quality.update(
                quality_grade="R3",
                quality_reason="invalid_native_price",
                formally_ingested=False,
                default_research_admitted=False,
                allowed_consumer="none",
            )
            bars = {}
        return quality, bars
    if interval != "5m":
        raise ValueError("required native interval absent")
    grid = m5_grid(day)
    quality = {
        "stock_code": code,
        "market_date": day,
        "quality_grade": "R3",
        "quality_reason": "original_source_unavailable",
        "formally_ingested": False,
        "default_research_admitted": False,
        "raw_sha256": digest(raw) if raw else None,
        "missing_actual_minutes": grid,
        "auction_source_observed": False,
        "allowed_consumer": "none",
    }
    if daily["tradestatus"] == "0":
        if _decimal(daily["volume"]) == 0:
            quality.update(
                quality_grade="SUSPENDED",
                quality_reason="sealed_daily_no_trade",
                missing_actual_minutes=[],
            )
        else:
            quality["quality_reason"] = "suspended_with_positive_daily_volume"
        return quality, {}
    if raw is None:
        return quality, {}
    try:
        bars = native_m5(raw, code, day)
        missing = [stamp for stamp in grid if stamp not in bars]
        quality["missing_actual_minutes"] = missing
        if not bars or grid[-1] not in bars:
            quality["quality_reason"] = "missing_or_wrong_terminal_close"
            return quality, {}
        if bars[grid[-1]]["close"].quantize(Decimal(".01")) != _decimal(daily["close"]).quantize(
            Decimal(".01")
        ):
            quality["quality_reason"] = "missing_or_wrong_terminal_close"
            return quality, {}
        if sum((bar["volume_shares"] for bar in bars.values()), Decimal(0)) != _decimal(
            daily["volume"]
        ):
            quality["quality_reason"] = "minute_daily_volume_mismatch"
            return quality, {}
        quality.update(
            quality_grade="R2" if missing else "R0",
            quality_reason="native_5m_restricted_gaps" if missing else "native_48_exact_daily",
            formally_ingested=True,
            default_research_admitted=True,
            allowed_consumer="gap_aware_non_execution_development",
        )
        return quality, bars
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        quality["quality_reason"] = "invalid_native_5m_source"
        return quality, {}


def source_index(
    root: Path,
    ref: dict[str, Any],
    interval: str,
) -> dict[str, tuple[Path, dict[str, Any]]]:
    result = {}
    expected_kind = (
        "p10_tencent_native_m1_raw_capture_shard"
        if interval == "1m"
        else "p10_baostock_native_m5_raw_capture_shard"
    )
    for shard in sorted(root.glob("shard-*")):
        meta = object_at(shard / "identity.json")
        if (
            meta.get("kind") != expected_kind
            or meta.get("market_date") != ref["market_date"]
            or meta.get("market_ods_manifest_sha256") != ref["source_ods_manifest_sha256"]
        ):
            raise ValueError("raw capture lacks exact daily/date identity")
        for code in meta["selected_symbols"]:
            if code not in ref["securities"] or code in result:
                raise ValueError("capture population conflict")
            receipt_path = shard / "receipts" / f"{code}.json"
            receipt = object_at(receipt_path) if receipt_path.exists() else {}
            if receipt and (
                receipt.get("stock_code") != code
                or receipt.get("status") != "raw_saved"
                or receipt.get("market_date") != ref["market_date"]
                or receipt.get("source_market_ods_sha256") != ref["source_ods_manifest_sha256"]
            ):
                raise ValueError("source receipt differs from exact daily identity")
            result[code] = (shard / "raw" / f"{code}.json.gz", receipt)
    return result


def minute_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            (name, pa.string())
            for name in ("stock_code", "market_date", "source_ts", "exchange_session_phase")
        ]
        + [
            (name, pa.decimal128(38, 10))
            for name in ("open", "high", "low", "close", "volume_shares", "amount")
        ]
        + [(name, pa.string()) for name in ("raw_sha256", "quality_grade", "quality_reason")]
        + [("original_source_observed", pa.bool_()), ("default_research_admitted", pa.bool_())]
    )


def build(
    *,
    reference_file: Path,
    capture_root: Path,
    interval: str,
    product_root: Path,
    code_revision: str,
    prior_capture_root: Path | None = None,
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ref = reference(reference_file)
    require_revision(code_revision)
    index = source_index(capture_root, ref, interval)
    if prior_capture_root is not None:
        prior = source_index(prior_capture_root, ref, interval)
        merged = dict(prior)
        for code, (path, receipt) in index.items():
            if not receipt:
                continue
            if code in prior and prior[code][1]:
                before = gzip.decompress(prior[code][0].read_bytes())
                after = gzip.decompress(path.read_bytes())
                if (
                    digest(before) != "sha256:" + prior[code][1]["raw_sha256"]
                    or digest(after) != "sha256:" + receipt["raw_sha256"]
                ):
                    raise ValueError("immutable captured original SHA differs")
                changed = target_source_changed(before, after, code, ref["market_date"], interval)
                if not changed:
                    continue
            merged[code] = (path, receipt)
        index = merged
    closure = digest(
        encoded(
            [
                ref["reference_digest"],
                interval,
                code_revision,
                [(code, receipt) for code, (_, receipt) in sorted(index.items())],
            ]
        )
    )
    destination = product_root / "packages" / ref["market_date"] / closure[7:23]
    if destination.exists():
        result = verify(destination)
        publish_pointer(product_root, result)
        return result
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".minute-", dir=destination.parent))
    schema = minute_schema()
    grades: Counter[str] = Counter()
    row_counts: Counter[str] = Counter()
    buffer = []
    try:
        shutil.copyfile(reference_file, stage / "daily-reference.json")
        (stage / "ods/raw").mkdir(parents=True)
        with (
            pq.ParquetWriter(
                stage / "minute-real-observed.parquet", schema, compression="zstd"
            ) as writer,
            (stage / "quality-status.jsonl").open("xb") as out,
        ):
            for code, daily in sorted(ref["securities"].items()):
                path, receipt = index.get(code, (Path("/nonexistent-p10-source"), {}))
                raw = gzip.decompress(path.read_bytes()) if receipt else None
                if raw is not None and digest(raw) != "sha256:" + receipt["raw_sha256"]:
                    raise ValueError("immutable captured original SHA differs")
                quality, bars = assess(
                    interval=interval, code=code, day=ref["market_date"], raw=raw, daily=daily
                )
                quality["source_acquired_at_or_after"] = receipt.get("captured_at")
                quality["reference_ods_manifest_sha256"] = ref["source_ods_manifest_sha256"]
                grades[quality["quality_grade"]] += 1
                out.write(encoded(quality))
                if quality["formally_ingested"]:
                    shutil.copyfile(path, stage / "ods/raw" / f"{code}.json.gz")
                    for stamp, bar in sorted(bars.items()):
                        buffer.append(
                            {
                                "stock_code": code,
                                "market_date": ref["market_date"],
                                "source_ts": datetime.strptime(stamp, "%Y%m%d%H%M").isoformat(" "),
                                "exchange_session_phase": "auction"
                                if stamp.endswith("0930")
                                else "continuous",
                                **bar,
                                "amount": bar.get("amount"),
                                "raw_sha256": quality["raw_sha256"],
                                "quality_grade": quality["quality_grade"],
                                "quality_reason": quality["quality_reason"],
                                "original_source_observed": True,
                                "default_research_admitted": True,
                            }
                        )
                        row_counts[quality["quality_grade"]] += 1
                        if len(buffer) >= 20000:
                            writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                            buffer.clear()
            if buffer:
                writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
        seal(
            stage,
            {
                "schema_version": 1,
                "kind": KIND,
                "market_date": ref["market_date"],
                "interval": interval,
                "policy_id": POLICY_ID,
                "code_revision": code_revision,
                "reference_digest": ref["reference_digest"],
                "source_ods_manifest_sha256": ref["source_ods_manifest_sha256"],
                "grades": dict(grades),
                "rows_per_grade": dict(row_counts),
                "research_ready": grades["R3"] == 0 and sum(grades.values()) == ref["population"],
                "execution_authorized": False,
                "sealed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        result = verify(stage)
        os.replace(stage, destination)
        result["package_path"] = str(destination)
        publish_pointer(product_root, result)
        return result
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    manifest = verify_seal(root, KIND)
    require_revision(manifest.get("code_revision"))
    ref = reference(root / "daily-reference.json")
    interval = manifest["interval"]
    if (
        interval not in {"1m", "5m"}
        or manifest.get("policy_id") != POLICY_ID
        or manifest.get("reference_digest") != ref["reference_digest"]
        or manifest.get("market_date") != ref["market_date"]
        or manifest.get("source_ods_manifest_sha256") != ref["source_ods_manifest_sha256"]
        or manifest.get("execution_authorized") is not False
    ):
        raise ValueError("minute package policy/reference differs")
    statuses = {}
    grades: Counter[str] = Counter()
    for line in (root / "quality-status.jsonl").read_text().splitlines():
        status = json.loads(line)
        code = status["stock_code"]
        if code in statuses or code not in ref["securities"]:
            raise ValueError("duplicate/outside minute quality population")
        path = root / "ods/raw" / f"{code}.json.gz"
        admitted = status["quality_grade"] in ALLOWED
        raw = gzip.decompress(path.read_bytes()) if path.is_file() else None
        if admitted != (raw is not None) or status["quality_grade"] not in ALLOWED | {
            "R3",
            "SUSPENDED",
        }:
            raise ValueError("rejected original entered formal source or admitted source absent")
        if admitted or status["quality_grade"] == "SUSPENDED":
            expected, _ = assess(
                interval=interval,
                code=code,
                day=ref["market_date"],
                raw=raw,
                daily=ref["securities"][code],
            )
            if any(
                status.get(key) != value
                for key, value in expected.items()
                if key not in {"source_acquired_at_or_after", "raw_sha256"}
            ):
                raise ValueError("physical original does not support assigned quality")
            if admitted:
                if raw is None:
                    raise ValueError("admitted source absent")
                acquired = datetime.fromisoformat(status["source_acquired_at_or_after"])
                if (
                    status["raw_sha256"] != digest(raw)
                    or acquired.tzinfo is None
                    or acquired.astimezone(CST).date() < date.fromisoformat(ref["market_date"])
                ):
                    raise ValueError("source SHA or actual knowledge time invalid")
        if status.get("reference_ods_manifest_sha256") != ref["source_ods_manifest_sha256"]:
            raise ValueError("quality row daily binding differs")
        statuses[code] = status
        grades[status["quality_grade"]] += 1
    if set(statuses) != set(ref["securities"]) or dict(grades) != manifest["grades"]:
        raise ValueError("minute status census differs")
    counts: Counter[str] = Counter()
    visited = set()
    current = None
    remaining: dict[str, dict[str, Decimal]] = {}
    parquet = pq.ParquetFile(root / "minute-real-observed.parquet")
    if parquet.schema_arrow != minute_schema():
        raise ValueError("native minute Decimal schema differs")
    for batch in parquet.iter_batches(batch_size=20000):
        for row in batch.to_pylist():
            code = row["stock_code"]
            if code != current:
                if remaining or code in visited:
                    raise ValueError("missing/unordered/duplicate native physical rows")
                visited.add(code)
                current = code
                quality = statuses.get(code, {})
                raw = gzip.decompress((root / "ods/raw" / f"{code}.json.gz").read_bytes())
                _, remaining = assess(
                    interval=interval,
                    code=code,
                    day=ref["market_date"],
                    raw=raw,
                    daily=ref["securities"][code],
                )
            stamp = datetime.fromisoformat(row["source_ts"]).strftime("%Y%m%d%H%M")
            original = remaining.pop(stamp, None)
            if (
                original is None
                or row["market_date"] != ref["market_date"]
                or row["quality_grade"] != quality["quality_grade"]
                or row["quality_reason"] != quality["quality_reason"]
                or row["quality_grade"] not in ALLOWED
                or row["raw_sha256"] != quality["raw_sha256"]
                or row["original_source_observed"] is not True
                or row["default_research_admitted"] is not True
                or row["exchange_session_phase"]
                != ("auction" if stamp.endswith("0930") else "continuous")
                or any(
                    row[name] != original.get(name)
                    for name in ("open", "high", "low", "close", "volume_shares", "amount")
                )
            ):
                raise ValueError("native physical bars differ from actual source")
            counts[row["quality_grade"]] += 1
    if (
        remaining
        or visited
        != {code for code, value in statuses.items() if value["quality_grade"] in ALLOWED}
        or dict(counts) != manifest["rows_per_grade"]
        or manifest.get("research_ready") is not (grades["R3"] == 0)
    ):
        raise ValueError("minute row/grade/readiness census differs")
    return {
        "market_date": ref["market_date"],
        "interval": interval,
        "manifest_sha256": file_digest(root / "manifest.json"),
        "content_digest": manifest["content_digest"],
        "quality_grades": dict(grades),
        "row_counts": dict(counts),
        "research_ready": grades["R3"] == 0,
        "reference_digest": ref["reference_digest"],
        "package_path": str(root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "build", "verify"])
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--interval", choices=["1m", "5m"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument("--prior-capture-root", type=Path)
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--code-revision")
    args = parser.parse_args()
    if args.action == "verify":
        result = verify(args.output)
    elif args.action == "build":
        result = build(
            reference_file=args.reference,
            capture_root=args.capture_root,
            interval=args.interval,
            product_root=args.output,
            code_revision=args.code_revision,
            prior_capture_root=args.prior_capture_root,
        )
    else:
        ref = reference(args.reference)
        codes = sorted(ref["securities"])
        if args.codes_file:
            codes = json.loads(args.codes_file.read_text())
            if codes != sorted(set(codes)) or not set(codes) <= set(ref["securities"]):
                raise ValueError("revision cohort outside sealed daily reference")
        capture = capture_m1 if args.interval == "1m" else capture_m5
        result = capture(
            codes=codes,
            market_date=date.fromisoformat(ref["market_date"]),
            market_manifest_sha=ref["source_ods_manifest_sha256"],
            output=args.output,
            shard_index=args.shard_index,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
