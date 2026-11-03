"""New processing binding of observed native 5m bytes to a corrected daily source.

This never fabricates a capture receipt or edits the archived reference identity.
The exact-share comparison and terminal-close rule remain the existing rules.
"""

from __future__ import annotations

import gzip
import importlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import canonical, file_sha
from .base22_sources import disjoint_output, physical_path
from .daily_base24_market_resume import stamp
from .daily_base24_precision import _sha, _write, verify_market_revision
from .native5m_daily_source import KIND, _digest, m5_grid, minute_schema, native_m5

CONTRACT = "p10-cn02-native5m-source-rebinding-v1"


def qualify(raw: bytes | None, code: str, day: str, daily: dict[str, Any]) -> tuple[Any, Any]:
    """Same native-5m grid, cent-close and exact-share gates as the pinned source."""
    if raw:
        try:
            representation = json.loads(raw)
        except (ValueError, TypeError):
            representation = {}
        if (
            isinstance(representation, dict)
            and representation.get("provider") == "baostock+tencent-observed-closing-auction-5m"
        ):
            raise ValueError("NATIVE_REBIND_COMPOUND_SOURCE_REQUIRES_AUCTION_REPLAY")
    result = dict(
        stock_code=code,
        market_date=day,
        quality_grade="R3",
        quality_reason="original_source_unavailable",
        formally_ingested=False,
        default_research_admitted=False,
        raw_sha256=_sha(raw) if raw else None,
        missing_actual_minutes=m5_grid(day),
        auction_source_observed=False,
        allowed_consumer="none",
    )
    if daily["tradestatus"] == "0":
        if daily.get("daily_price_observation_state") == "unavailable_suspended":
            result.update(
                quality_grade="SUSPENDED",
                quality_reason="sealed_daily_suspension_price_unavailable",
                missing_actual_minutes=[],
                daily_price_observation_state="unavailable_suspended",
            )
        elif Decimal(daily["volume"]) == 0:
            result.update(
                quality_grade="SUSPENDED",
                quality_reason="sealed_daily_no_trade",
                missing_actual_minutes=[],
            )
        if raw and any(b["volume_shares"] > 0 for b in native_m5(raw, code, day).values()):
            raise ValueError("NATIVE_REBIND_SUSPENDED_POSITIVE_VOLUME")
        return result, {}
    if raw is None:
        return result, {}
    try:
        bars = native_m5(raw, code, day)
        missing = [x for x in m5_grid(day) if x not in bars]
        result["missing_actual_minutes"] = missing
        terminal = m5_grid(day)[-1]
        if terminal not in bars or bars[terminal]["close"].quantize(Decimal(".01")) != Decimal(
            daily["close"]
        ).quantize(Decimal(".01")):
            result["quality_reason"] = "missing_or_wrong_terminal_close"
            return result, {}
        if sum((b["volume_shares"] for b in bars.values()), Decimal(0)) != Decimal(daily["volume"]):
            result["quality_reason"] = "minute_daily_volume_mismatch"
            return result, {}
        result.update(
            quality_grade="R2" if missing else "R0",
            quality_reason="native_5m_restricted_gaps" if missing else "native_48_exact_daily",
            formally_ingested=True,
            default_research_admitted=True,
            allowed_consumer="gap_aware_non_execution_development",
        )
        return result, bars
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        result["quality_reason"] = "invalid_native_5m_source"
        return result, {}


def build(
    *,
    market: Path,
    reference: Path,
    originals: dict[str, dict[str, Any]],
    output: Path,
    revision: str,
) -> dict[str, Any]:
    """One bounded date; original entry carries exact bytes/hash and clock evidence."""
    market, reference = physical_path(market), physical_path(reference)
    roots = [market, reference] + [physical_path(Path(x["path"])) for x in originals.values()]
    disjoint_output(output, roots)
    output = physical_path(output)
    if output.exists() or len(originals) > 6000:
        raise ValueError("NATIVE_REBIND_NEW_BOUNDED_OUTPUT_REQUIRED")
    proof = verify_market_revision(market)
    ref = json.loads(reference.read_bytes())
    if ref["source_ods_manifest_sha256"] != proof["manifest_sha256"] or (
        ref["reference_digest"]
        != _digest({k: v for k, v in ref.items() if k != "reference_digest"})
        or not set(originals) <= set(ref["securities"])
    ):
        raise ValueError("NATIVE_REBIND_REFERENCE_DIFFERS")
    day = ref["market_date"]
    frozen = {str(p): file_sha(p) for p in roots if p.is_file()}
    for code, original in originals.items():
        p = Path(original["path"])
        original_raw = gzip.decompress(p.read_bytes())
        if (
            file_sha(p) != original["compressed_sha256"]
            or _sha(original_raw) != original["raw_sha256"]
        ):
            raise ValueError("NATIVE_REBIND_ORIGINAL_CHANGED")
        evidence = original["evidence"]
        if (
            evidence.get("raw_sha256", "").removeprefix("sha256:") != _sha(original_raw)[7:]
            or evidence.get("stock_code") != code
            or evidence.get("market_date") != day
        ):
            raise ValueError("NATIVE_REBIND_ORIGINAL_RECEIPT_DIFFERS")
        stamp(original["observed_at_or_after"])
    output.mkdir()
    (output / "ods/raw").mkdir(parents=True)
    (output / "evidence").mkdir()
    shutil.copytree(market, output / "evidence/market", copy_function=shutil.copyfile)
    shutil.copyfile(reference, output / "daily-reference.json")
    pa, pq = importlib.import_module("pyarrow"), importlib.import_module("pyarrow.parquet")
    grades: Counter[str] = Counter()
    row_counts: Counter[str] = Counter()
    origins = {}
    buffer = []
    with (
        pq.ParquetWriter(
            output / "minute-real-observed.parquet", minute_schema(), compression="zstd"
        ) as writer,
        (output / "quality-status.jsonl").open("xb") as status_file,
    ):
        for code, daily in sorted(ref["securities"].items()):
            entry = originals.get(code)
            raw = gzip.decompress(Path(entry["path"]).read_bytes()) if entry else None
            quality, bars = qualify(raw, code, day, daily)
            quality.update(
                source_acquired_at_or_after=entry["observed_at_or_after"] if entry else None,
                reference_ods_manifest_sha256=ref["source_ods_manifest_sha256"],
            )
            grades[quality["quality_grade"]] += 1
            status_file.write(canonical(quality) + b"\n")
            if quality["formally_ingested"]:
                assert entry is not None
                target = output / "ods/raw" / (code + ".json.gz")
                shutil.copyfile(Path(entry["path"]), target)
                origins[code] = {k: v for k, v in entry.items() if k != "path"}
                for label, bar in sorted(bars.items()):
                    buffer.append(
                        dict(
                            stock_code=code,
                            market_date=day,
                            source_ts=datetime.strptime(label, "%Y%m%d%H%M").isoformat(" "),
                            exchange_session_phase="continuous",
                            **bar,
                            raw_sha256=quality["raw_sha256"],
                            quality_grade=quality["quality_grade"],
                            quality_reason=quality["quality_reason"],
                            original_source_observed=True,
                            default_research_admitted=True,
                        )
                    )
                    row_counts[quality["quality_grade"]] += 1
                    if len(buffer) >= 8192:
                        writer.write_table(pa.Table.from_pylist(buffer, schema=minute_schema()))
                        buffer.clear()
        if buffer:
            writer.write_table(pa.Table.from_pylist(buffer, schema=minute_schema()))
    _write(
        output / "evidence/original-bindings.json",
        dict(
            kind=CONTRACT,
            rows=origins,
            new_processing_binding=True,
            original_capture_receipts_rewritten=False,
        ),
    )
    files = [
        dict(path=p.relative_to(output).as_posix(), bytes=p.stat().st_size, sha256=file_sha(p))
        for p in sorted(output.rglob("*"))
        if p.is_file()
    ]
    manifest = dict(
        schema_version=1,
        kind=KIND,
        processing_revision=CONTRACT,
        code_revision=revision,
        interval="5m",
        market_date=day,
        policy_id="p10-trading-day-complete-daily-v1",
        reference_digest=ref["reference_digest"],
        source_ods_manifest_sha256=ref["source_ods_manifest_sha256"],
        grades=dict(grades),
        rows_per_grade=dict(row_counts),
        research_ready=grades["R3"] == 0,
        execution_authorized=False,
        consumer_admission="NOT_GRANTED",
        sealed_at=datetime.now(timezone.utc).isoformat(),
        files=files,
        content_digest=_digest(files),
    )
    _write(output / "manifest.json", manifest)
    for name, sha in frozen.items():
        if file_sha(Path(name)) != sha:
            raise ValueError("NATIVE_REBIND_INPUT_CHANGED_DURING_BUILD")
    with (output / "READY").open("x") as stream:
        stream.write(file_sha(output / "manifest.json") + "\n")
    return dict(
        output=str(output),
        manifest_sha256=file_sha(output / "manifest.json"),
        grades=dict(grades),
        qualified=grades["R3"] == 0,
        rows_per_grade=dict(row_counts),
        old_sources_unchanged=True,
    )


def verify_binding(root: Path, ref: dict[str, Any], statuses: dict[str, Any]) -> None:
    """Called by the complete native verifier; all evidence is inside its mirror."""
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest.get("processing_revision") != CONTRACT:
        raise ValueError("NATIVE_REBIND_PROCESSING_CONTRACT_INVALID")
    proof = verify_market_revision(root / "evidence/market")
    if proof["manifest_sha256"] != ref["source_ods_manifest_sha256"]:
        raise ValueError("NATIVE_REBIND_MARKET_IDENTITY_DIFFERS")
    origins = json.loads((root / "evidence/original-bindings.json").read_bytes())
    admitted = {c for c, q in statuses.items() if q["quality_grade"] in {"R0", "R2"}}
    if (
        origins.get("kind") != CONTRACT
        or set(origins["rows"]) != admitted
        or origins.get("new_processing_binding") is not True
        or origins.get("original_capture_receipts_rewritten") is not False
    ):
        raise ValueError("NATIVE_REBIND_ORIGIN_POPULATION_DIFFERS")
    for code, entry in origins["rows"].items():
        p = root / "ods/raw" / (code + ".json.gz")
        q = statuses[code]
        old = entry["evidence"]
        if (
            file_sha(p) != entry["compressed_sha256"]
            or entry["raw_sha256"] != q["raw_sha256"]
            or old.get("raw_sha256", "").removeprefix("sha256:") != q["raw_sha256"][7:]
            or entry["observed_at_or_after"] != q["source_acquired_at_or_after"]
            or entry["observed_at_or_after"]
            != old.get("captured_at", old.get("source_acquired_at_or_after"))
        ):
            raise ValueError("NATIVE_REBIND_ORIGINAL_EVIDENCE_DIFFERS")
    market = root / "evidence/market"
    release = json.loads((market / "release.json").read_bytes())
    artifact = next(a for a in release["artifacts"] if a["name"] == "daily_ohlcv_none_delta")
    pq = importlib.import_module("pyarrow.parquet")
    rows = pq.ParquetFile(market / artifact["relative_path"]).read().to_pylist()
    seen = set()
    for row in rows:
        code = row["stock_code"]
        if code in seen or code not in ref["securities"]:
            raise ValueError("NATIVE_REBIND_REFERENCE_MARKET_KEYS_DIFFER")
        seen.add(code)
        if any(Decimal(row[k]) != Decimal(ref["securities"][code][k]) for k in ["close", "volume"]):
            raise ValueError("NATIVE_REBIND_REFERENCE_MARKET_VALUES_DIFFER")
    if seen != {c for c, d in ref["securities"].items() if d["close"] is not None}:
        raise ValueError("NATIVE_REBIND_REFERENCE_MARKET_KEYS_DIFFER")
