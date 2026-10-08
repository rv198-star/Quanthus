"""P10 v2: physical R2 warehouse admission and hard R3 source quarantine.

Reclassify immutable, independently verified loss-aware v1 products into a new
date-specific ODS/DWD product. R2 *real observed* minute rows enter DWD with
explicit restrictions. R3 has neither original raw bytes nor OHLCV rows in the
new formal product; source evidence stays in the old read-only forensic root.

This does not modify v1 seals, strict 240-minute data, daily-core or P6.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.pipelines.loss_aware_m1_publication import (
    _source_dispositions,
    verify_source_ods,
)
from quanthus.pipelines.loss_aware_m1_research import (
    _board,
    _reference,
    verify_package,
)
from quanthus.providers.loss_aware_m1 import _grid, _source_rows

PRODUCT = "intraday-1m-loss-aware-v2"
POLICY = (
    Path(__file__).resolve().parents[3]
    / "config/pipelines/warehouse-minute-loss-aware-admission.v2.json"
)
R0_R1 = frozenset({"R0", "R1"})
FORMAL = frozenset({"R0", "R1", "R2"})
OTHER = frozenset({"R3", "SUSPENDED"})
ALLOWED = frozenset({
    "2026-09-24", "2026-09-28", "2026-09-29",
    "2026-09-30", "2026-10-08",
})


def route_grade(grade: str) -> tuple[bool, bool]:
    """(formal warehouse source/bar intake, default research admission)."""
    if grade in R0_R1:
        return True, True
    if grade == "R2":
        return True, False
    if grade in OTHER:
        return False, False
    raise ValueError("unknown 1m admission grade")


def _bytes(obj: object) -> bytes:
    return (
        json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(piece)
    return "sha256:" + h.hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as handle:
        handle.write(_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _inventory(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha(path),
        }
        for path in sorted(paths)
    ]


def _verify_members(root: Path, manifest: dict[str, Any], seal: str) -> None:
    if (root / seal).read_text().strip() != _sha(root / "manifest.json"):
        raise ValueError("v2 product seal differs from manifest SHA")
    expected = {"manifest.json", seal}
    for item in manifest["files"]:
        rel = item["path"]
        path = root / rel
        if (
            not isinstance(rel, str)
            or rel.startswith("/")
            or not path.resolve().is_relative_to(root.resolve())
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item["bytes"]
            or _sha(path) != item["sha256"]
            or rel in expected
        ):
            raise ValueError("v2 product source/member file integrity failure")
        expected.add(rel)
    actual = {
        f.relative_to(root).as_posix()
        for f in root.rglob("*")
        if f.is_file()
    }
    if actual != expected:
        raise ValueError("v2 product unexpected or missing physical member")


def _status_record(raw: dict[str, Any], old_ods: Path) -> dict[str, Any]:
    grade = raw["quality_grade"]
    store, default_admit = route_grade(grade)
    source_sha = raw.get("source_raw_sha256")
    if store and not source_sha:
        raise ValueError("R0/R1/R2 cannot enter without original source SHA")
    record = {
        "stock_code": raw["stock_code"],
        "market_date": raw["market_date"],
        "quality_grade": grade,
        "quality_reason": raw.get("reason"),
        "missing_actual_minutes": raw.get("missing_actual_minutes", []),
        "critical_gap": bool(raw.get("critical_gap", False)),
        "auction_source_observed": bool(raw.get("auction_source_observed", False)),
        "raw_sha256": source_sha,
        "source_acquired_at_or_after": raw.get("source_acquired_at_or_after"),
        "source_semantic_disagreement": raw.get("source_semantic_disagreement"),
        "daily_share_delta": raw.get("daily_share_delta"),
        "daily_close_delta": raw.get("daily_close_delta"),
        "amount_quality": raw.get("amount_quality"),
        "formally_ingested": store,
        "default_research_admitted": default_admit,
        "strict_240_source_qualified": False,
        "allowed_consumer": (
            "gap_aware_non_execution_development"
            if default_admit else "explicit_r2_review_only" if store else "none"
        ),
    }
    if grade == "R3":
        # Reference the pre-existing forensic evidence, not the original R3 bytes.
        record["quarantine_evidence_ref"] = str(old_ods)
    return record


def _validate_r2_native(
    raw: bytes,
    *,
    code: str,
    day: str,
    daily_shares: object,
    daily_close: object,
    disposition: dict[str, Any],
) -> tuple[tuple[str, dict[str, Decimal]], ...]:
    """R2 must be original physically valid observations, not unlabeled fills."""
    entries, sha = _source_rows(raw, code, day, period="m1")
    if disposition["source_raw_sha256"] != sha:
        raise ValueError("R2 original response SHA differs from locked grade")
    grid = _grid(day)
    if not entries or not set(entries) <= set(grid) | {day.replace("-", "") + "0930"}:
        raise ValueError("R2 observation outside governed exchange session")
    missing = [stamp for stamp in grid if stamp not in entries]
    if missing != disposition["missing_actual_minutes"]:
        raise ValueError("R2 missing original minute timestamps differ from quality ledger")
    if len(entries) != disposition["observed_native_rows"]:
        raise ValueError("R2 native row count differs from originally graded source")
    total = sum((row["volume"] for row in entries.values()), Decimal(0))
    if total != Decimal(str(daily_shares)):
        raise ValueError("R2 source/daily share volume mismatches: must stay R3")
    end = entries.get(grid[-1])
    if end is None or end["close"].quantize(Decimal("0.01")) != Decimal(
        str(daily_close)
    ).quantize(Decimal("0.01")):
        raise ValueError("R2 source lacks authoritative closing price")
    return tuple(sorted(entries.items()))


def _new_row(
    *,
    code: str,
    day: str,
    stamp: str,
    bar: dict[str, Decimal],
    graded: dict[str, Any],
) -> dict[str, Any]:
    return {
        "stock_code": code,
        "market_date": day,
        "source_ts": (
            f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} "
            f"{stamp[8:10]}:{stamp[10:12]}:00"
        ),
        "exchange_session_phase": "auction" if stamp.endswith("0930") else "continuous",
        "interval_size": "1m",
        "price_basis": "none",
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "volume_shares": bar["volume"],
        "amount": None,
        "raw_sha256": graded["raw_sha256"],
        "quality_grade": graded["quality_grade"],
        "original_source_observed": True,
        "cross_source_status": graded["source_semantic_disagreement"],
    }


def _research_ready(
    grades: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> bool:
    by_board: dict[str, Counter[str]] = defaultdict(Counter)
    for code, entry in grades.items():
        by_board[_board(code)][entry["quality_grade"]] += 1
    total = Counter(e["quality_grade"] for e in grades.values())
    admitted = total["R0"] + total["R1"]
    traded = len(grades) - total["SUSPENDED"]
    limit = policy["thresholds"]
    if traded == 0 or Decimal(admitted) / Decimal(traded) < Decimal(
        limit["min_traded_research_fraction"]
    ):
        return False
    for count in by_board.values():
        denominator = sum(count.values()) - count["SUSPENDED"]
        if denominator and (
            Decimal(count["R0"] + count["R1"]) / Decimal(denominator)
            < Decimal(limit["min_board_research_fraction"])
        ):
            return False
    return True


def _load_v1(
    v1_root: Path,
    day: str,
) -> tuple[Path, Path, dict[str, dict[str, Any]], dict[str, Any]]:
    ref = json.loads((v1_root / "date-status" / f"{day}.json").read_text())
    source = v1_root / ref["source_ods_path"]
    old_dwd = v1_root / ref["research_dwd_path"]
    verify_source_ods(source)
    checked = verify_package(old_dwd)
    if checked["market_date"] != day or checked["product_scope"] != ref["product_scope"]:
        raise ValueError("v1 source/day identities differ")
    if (
        checked["manifest_sha256"] != ref["research_dwd_manifest_sha256"]
        or ref["market_date"] != day
    ):
        raise ValueError("old source-backed DWD manifest binding mismatch")
    original = _source_dispositions(old_dwd, day)
    if _sha(source / "source-quality.jsonl") != _sha(old_dwd / "security-quality.jsonl"):
        raise ValueError("v1 ODS/DWD original grade ledger differs")
    if set(original) != set(f"{n:06}" for n in range(0, 999999) if False):
        pass  # Membership verified against daily source below, not numeric ranges.
    return source, old_dwd, original, ref


def verify_v2(root: Path) -> dict[str, Any]:
    """Verify physically stored R2 and *zero* R3 raw/bar admission."""
    import pyarrow.parquet as pq

    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("kind") != "p10_1m_r2_stored_r3_excluded_v2":
        raise ValueError("wrong warehouse admission v2 kind")
    _verify_members(root, manifest, "SEALED")
    quality = root / "quality-status.jsonl"
    statuses = {}
    for line in quality.read_text(encoding="ascii").splitlines():
        record = json.loads(line)
        code = record["stock_code"]
        if code in statuses:
            raise ValueError("duplicate quality identifier")
        store, consume = route_grade(record["quality_grade"])
        if record.get("formally_ingested") is not store or (
            record.get("default_research_admitted") is not consume
        ):
            raise ValueError("warehouse/research gate differs from quality grade")
        statuses[code] = record
    if len(statuses) != 4992 or manifest["total_securities"] != 4992:
        raise ValueError("warehouse status denominator must be original complete universe")
    raw_count = 0
    for code, record in statuses.items():
        stored = root / "ods" / "raw" / f"{code}.json.gz"
        allowed = record["formally_ingested"]
        if stored.exists() != allowed:
            raise ValueError("R3 or suspended raw physically admitted, or valid source missing")
        if allowed:
            if stored.is_symlink():
                raise ValueError("warehouse original source must not be symlink")
            if "sha256:" + hashlib.sha256(gzip.decompress(stored.read_bytes())).hexdigest() != (
                "sha256:" + record["raw_sha256"]
            ):
                raise ValueError("warehouse source SHA changed")
            raw_count += 1
    grade_rows = Counter()
    per_r2 = Counter()
    source_grade = {c: r["quality_grade"] for c, r in statuses.items()}
    parquet = pq.ParquetFile(root / "dwd_security_bar_1m_loss_aware_v2.parquet")
    for batch in parquet.iter_batches(
        batch_size=20000,
        columns=[
            "stock_code", "quality_grade", "market_date", "original_source_observed",
            "formally_ingested", "default_research_admitted", "quality_reason",
            "quality_missing_actual_minutes_json", "raw_sha256",
        ],
    ):
        for row in batch.to_pylist():
            code = row["stock_code"]
            grade = row["quality_grade"]
            if (
                source_grade.get(code) != grade
                or grade not in FORMAL
                or row["market_date"] != manifest["market_date"]
                or not row["original_source_observed"]
                or not row["formally_ingested"]
                or row["default_research_admitted"] is not (grade in R0_R1)
                or row["raw_sha256"] != statuses[code]["raw_sha256"]
                or row["quality_reason"] != statuses[code]["quality_reason"]
                or json.loads(row["quality_missing_actual_minutes_json"])
                != statuses[code]["missing_actual_minutes"]
            ):
                raise ValueError("invalid R2/R3 physical DWD admission or source grade mismatch")
            grade_rows[grade] += 1
            if grade == "R2":
                per_r2[code] += 1
    for code, item in statuses.items():
        if item["quality_grade"] == "R2" and per_r2[code] == 0:
            raise ValueError("R2 status lacks physically admitted observed minute rows")
    if (
        sum(grade_rows.values()) != manifest["observed_rows"]
        or dict(grade_rows) != {k: v for k, v in manifest["rows_per_grade"].items() if v}
        or raw_count != manifest["admitted_raw_count"]
    ):
        raise ValueError("physical source/DWD grade totals do not match sealed manifest")
    if any((root / "ods" / "raw" / f"{code}.json.gz").exists()
           for code, r in statuses.items() if r["quality_grade"] == "R3"):
        raise ValueError("R3 original source landed in warehouse")
    return {
        "market_date": manifest["market_date"],
        "product_scope": PRODUCT,
        "source_sha256": _sha(root / "manifest.json"),
        "admitted_raw_count": raw_count,
        "grade_rows": dict(grade_rows),
        "research_ready": manifest["research_ready"],
    }


def build_v2(
    *,
    day: str,
    old_root: Path,
    daily_release: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Rebuild from sealed source facts, never source-poll and never modify v1."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if day not in ALLOWED:
        raise ValueError("not an authorized historical 1m day")
    if output_root.resolve().is_relative_to(old_root.resolve()) or (
        output_root.resolve().is_relative_to(
            Path("/srv/quanthus/dwd-dws-v2/daily").resolve()
        )
    ):
        raise ValueError("v2 cannot replace legacy v1 or daily source assets")
    if not POLICY.is_file():
        raise ValueError("versioned v2 policy missing")
    policy = json.loads(POLICY.read_text())
    if (
        policy["policy_id"] != "quanthus-1m-loss-aware-r2-storage-v2"
        or policy["product_scope"] != PRODUCT
    ):
        raise ValueError("unexpected admission policy revision")
    source, old_dwd, original, old_receipt = _load_v1(old_root, day)
    daily, daily_sha, states = _reference(daily_release, day)
    if len(daily) != 4992 or set(daily) != set(original):
        raise ValueError("sealed warehouse v1 source securities differ from daily reference")
    old_manifest = json.loads((old_dwd / "manifest.json").read_text())
    if old_manifest["source_daily_manifest_sha256"] != daily_sha:
        raise ValueError("daily price-basis identity differs from source evidence")
    counts = Counter(r["quality_grade"] for r in original.values())
    if dict(counts) != old_manifest["grades"]:
        raise ValueError("original assessment grade counts unexpectedly drifted")
    revision = hashlib.sha256(
        _bytes([_sha(POLICY), old_receipt["research_dwd_manifest_sha256"]])
    ).hexdigest()[:16]
    destination = output_root / "packages" / day / revision
    status_file = output_root / "date-status" / f"{day}.json"
    if destination.exists() or status_file.exists():
        if not destination.exists() or not status_file.exists():
            raise ValueError("partial versioned research publication, require bounded recovery")
        receipt = json.loads(status_file.read_text())
        if receipt.get("manifest_sha256") != _sha(destination / "manifest.json"):
            raise ValueError("existing immutable date status/manifest disagrees")
        return verify_v2(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".r2-v2-", dir=destination.parent))
    try:
        (work / "ods" / "raw").mkdir(parents=True)
        new_ledger = work / "quality-status.jsonl"
        ods_members: list[Path] = []
        status = {}
        with new_ledger.open("x", encoding="ascii") as stream:
            for code in sorted(original):
                old_grade = original[code]
                record = _status_record(old_grade, source)
                if (states[code] == "0") != (record["quality_grade"] == "SUSPENDED"):
                    if record["quality_grade"] != "R3":
                        raise ValueError("no-trade/source-grade discrepancy")
                stream.write(_bytes(record).decode("ascii"))
                status[code] = record
                if record["formally_ingested"]:
                    old_raw = source / "raw" / f"{code}.json.gz"
                    copied = work / "ods" / "raw" / f"{code}.json.gz"
                    compressed = old_raw.read_bytes()
                    raw_sha = hashlib.sha256(gzip.decompress(compressed)).hexdigest()
                    if raw_sha != record["raw_sha256"]:
                        raise ValueError("admitted raw source SHA differs from original")
                    with copied.open("xb") as f:
                        f.write(compressed)
                    ods_members.append(copied)
            stream.flush()
            os.fsync(stream.fileno())

        original_parquet = pq.ParquetFile(
            old_dwd / "dwd_security_bar_1m_loss_aware.parquet"
        )
        schema = original_parquet.schema_arrow
        extra = [
            pa.field("quality_reason", pa.string()),
            pa.field("quality_missing_actual_minutes_json", pa.string()),
            pa.field("quality_critical_gap", pa.bool_()),
            pa.field("formally_ingested", pa.bool_()),
            pa.field("default_research_admitted", pa.bool_()),
        ]
        schema = pa.schema([*schema, *extra])
        dwd_file = work / "dwd_security_bar_1m_loss_aware_v2.parquet"
        writer = pq.ParquetWriter(dwd_file, schema, compression="zstd")
        rows_per_grade: Counter[str] = Counter()
        batch_rows: list[dict[str, Any]] = []

        def push(row: dict[str, Any]) -> None:
            entry = status[row["stock_code"]]
            grade = entry["quality_grade"]
            if grade not in FORMAL or row["quality_grade"] != grade:
                raise ValueError("cannot admit old source row under new mismatched grade")
            if (
                row["market_date"] != day
                or row["raw_sha256"] != entry["raw_sha256"]
                or row.get("original_source_observed") is not True
            ):
                raise ValueError("DWD row provenance/date or observed source does not agree")
            row["quality_reason"] = entry["quality_reason"]
            row["quality_missing_actual_minutes_json"] = json.dumps(
                entry["missing_actual_minutes"], separators=(",", ":")
            )
            row["quality_critical_gap"] = entry["critical_gap"]
            row["formally_ingested"] = True
            row["default_research_admitted"] = grade in R0_R1
            batch_rows.append(row)
            rows_per_grade[grade] += 1
            if len(batch_rows) >= 20000:
                writer.write_table(pa.Table.from_pylist(batch_rows, schema=schema))
                batch_rows.clear()

        try:
            for batch in original_parquet.iter_batches(batch_size=16384):
                for row in batch.to_pylist():
                    push(row)
            for code in sorted(original):
                entry = status[code]
                if entry["quality_grade"] != "R2":
                    continue
                raw = gzip.decompress(
                    (work / "ods" / "raw" / f"{code}.json.gz").read_bytes()
                )
                observed = _validate_r2_native(
                    raw, code=code, day=day,
                    daily_shares=daily[code]["volume"],
                    daily_close=daily[code]["close"],
                    disposition=original[code],
                )
                if not observed:
                    raise ValueError("R2 had no source-native real minute observations")
                for stamp, bar in observed:
                    row = _new_row(
                        code=code, day=day, stamp=stamp, bar=bar, graded=entry,
                    )
                    push(row)
            if batch_rows:
                writer.write_table(pa.Table.from_pylist(batch_rows, schema=schema))
        finally:
            writer.close()

        research_ready = _research_ready(status, policy)
        summary = {
            "schema_version": 2,
            "kind": "p10_loss_aware_v2_quality_report",
            "market_date": day,
            "product_scope": PRODUCT,
            "grade_counts": dict(counts),
            "rows_per_grade": dict(rows_per_grade),
            "original_source_observation_only": True,
            "R2_physical_source_and_bar_admitted_restricted": counts["R2"],
            "R3_source_bar_ingested": 0,
            "R3_quarantine_metadata_only": counts["R3"],
            "suspended_metadata_only": counts["SUSPENDED"],
            "research_ready": research_ready,
            "default_research_admitted_grades": ["R0", "R1"],
            "new_policy_sha256": _sha(POLICY),
            "prior_v1_source_ods_manifest_sha256": _sha(source / "manifest.json"),
            "prior_v1_research_dwd_manifest_sha256": _sha(old_dwd / "manifest.json"),
            "daily_reference_manifest_sha256": daily_sha,
            "consumer": "gap-aware_non_execution_development_only",
        }
        report = work / "quality-summary.json"
        _write(report, summary)
        manifest = {
            "schema_version": 2,
            "kind": "p10_1m_r2_stored_r3_excluded_v2",
            "product_scope": PRODUCT,
            "market_date": day,
            "total_securities": len(original),
            "source_identity": _sha(source / "manifest.json"),
            "admitted_raw_count": sum(
                1 for r in status.values() if r["formally_ingested"]
            ),
            "quality_restriction_count": counts["R2"],
            "rejected_source_count": counts["R3"],
            "observed_rows": sum(rows_per_grade.values()),
            "rows_per_grade": dict(rows_per_grade),
            "research_ready": research_ready,
            "v1_research_release_manifest_sha256": _sha(old_dwd / "manifest.json"),
            "daily_reference_manifest_sha256": daily_sha,
            "policy_sha256": _sha(POLICY),
            "files": _inventory(work, [*ods_members, new_ledger, dwd_file, report]),
        }
        _write(work / "manifest.json", manifest)
        (work / "SEALED").write_text(_sha(work / "manifest.json") + "\n")
        verify_v2(work)
        os.replace(work, destination)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    result = verify_v2(destination)
    receipt = {
        "schema_version": 2,
        "market_date": day,
        "product_scope": PRODUCT,
        "manifest_sha256": result["source_sha256"],
        "package": destination.relative_to(output_root).as_posix(),
        "research_ready": result["research_ready"],
        "R2_stored_restricted": counts["R2"],
        "R3_source_excluded": counts["R3"],
        "source_safety": "R3 original raw remains only in prior forensic v1 root",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(status_file, receipt)
    if research_ready:
        pointer = output_root / "CURRENT_RESEARCH_1M_V2.json"
        if pointer.is_file():
            previous = json.loads(pointer.read_text())
            if date.fromisoformat(previous["market_date"]) >= date.fromisoformat(day):
                raise ValueError("v2 research pointer cannot retreat or overwrite accepted history")
        _write(pointer, receipt)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day")
    parser.add_argument("--v1-product-root", type=Path)
    parser.add_argument("--daily-market-release", type=Path)
    parser.add_argument("--v2-product-root", type=Path)
    parser.add_argument("--verify-package", type=Path)
    args = parser.parse_args(argv)
    if args.verify_package:
        print(json.dumps(verify_v2(args.verify_package), sort_keys=True))
        return 0
    if not all((args.day, args.v1_product_root, args.daily_market_release, args.v2_product_root)):
        parser.error("day, v1-product-root, daily-market-release and v2-product-root required")
    root = args.v2_product_root
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".writer.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        outcome = build_v2(
            day=args.day,
            old_root=args.v1_product_root,
            daily_release=args.daily_market_release,
            output_root=root,
        )
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
