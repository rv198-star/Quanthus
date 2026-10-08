"""Build a separate source-native loss-aware research 1m product from verified raw.

Never edits legacy ODS or DWD, never turns vendor-sparse 1m into 240 synthetic
OHLC observations and never claims PIT availability before original acquisition.
A product-specific READY is written only after actual date and board coverage
tests; every non-admitted stock remains explicitly in the disposition ledger.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import verify_ods_release
from quanthus.providers.loss_aware_m1 import TARGET, LossAssessment, assess

PRODUCT = "intraday-1m-research-loss-aware-v1"
POLICY_PATH = (
    Path(__file__).resolve().parents[3] / "config/pipelines/warehouse-minute-loss-aware.v1.json"
)
ARCHIVE_LEDGER = Path(
    "/root/work/astock/data/quarantine/minute-ledger/2026-09-24-strict-20260929T0250/records.jsonl"
)


def _sha(path: Path) -> str:
    obj = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            obj.update(chunk)
    return obj.hexdigest()


def _write(path: Path, raw: bytes) -> None:
    with path.open("xb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


def _board(code: str) -> str:
    if code.startswith(("688", "689")):
        return "STAR"
    if code.startswith(("300", "301")):
        return "ChiNext"
    if code.startswith("6"):
        return "SH-main"
    return "SZ-main"


def _reference(release: Path, day: str) -> tuple[dict[str, dict[str, Any]], str, dict[str, str]]:
    import pyarrow.parquet as pq

    verified = verify_ods_release(release)
    if verified.release.market_date.isoformat() != day or verified.release.schedule != "daily":
        raise ValueError("wrong-date or non-ready daily source")
    arts = {a.name: a for a in verified.release.artifacts}
    market = arts["daily_ohlcv_none_delta"]
    status = arts["daily_status_observation_delta"]
    daily_rows = pq.read_table(
        release / market.relative_path, columns=["stock_code", "volume", "close"]
    ).to_pylist()
    status_rows = pq.read_table(
        release / status.relative_path, columns=["stock_code", "tradestatus"]
    ).to_pylist()
    daily = {str(r["stock_code"]): r for r in daily_rows}
    statuses = {str(r["stock_code"]): str(r["tradestatus"]) for r in status_rows}
    if (
        len(daily) != 4992
        or len(daily_rows) != 4992
        or len(statuses) != 4992
        or len(status_rows) != 4992
        or set(daily) != set(statuses)
        or any(s not in {"0", "1"} for s in statuses.values())
    ):
        raise ValueError("source daily universe/identity/status mismatch")
    return daily, verified.manifest_sha256, statuses


def _archived_source_ledger(path: Path = ARCHIVE_LEDGER) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for text in path.read_text().splitlines():
        row = json.loads(text)
        code = str(row["stock_code"])
        source = next(
            (x for x in row["sources"] if x["provider"] == "tencent-mkline-validated"), None
        )
        if code in result or source is None:
            raise ValueError("archived ledger duplicate or missing Tencent raw")
        result[code] = {
            "raw_sha256": str(source["raw_sha256"]),
            "cross_source_state": (
                "known_semantic_conflict"
                if row["disposition"] == "blocked_cross_source_conflict"
                else "single_qualified_source"
            ),
        }
    if len(result) != 4992:
        raise ValueError("strict archived 1m evidence ledger does not cover universe")
    return result


def _native_raw(
    root: Path,
    code: str,
    *,
    day: str,
    mode: str,
    ledger: dict[str, dict[str, str]] | None,
) -> tuple[bytes | None, str | None, str, str | None]:
    if mode == "archive":
        path = root / f"{code}.json.gz"
        if not path.is_file() or ledger is None:
            return None, None, "single_qualified_source", None
        raw = gzip.decompress(path.read_bytes())
        sha = hashlib.sha256(raw).hexdigest()
        if sha != ledger[code]["raw_sha256"]:
            raise ValueError(f"old sealed source SHA mismatch for {code}")
        return raw, sha, ledger[code]["cross_source_state"], "2026-09-28"
    path = root / "raw" / f"{code}.json.gz"
    receipt_path = root / "receipts" / f"{code}.json"
    if not receipt_path.is_file():
        return None, None, "not_independently_compared", None
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != "raw_saved" or not path.is_file():
        return None, None, "not_independently_compared", None
    raw = gzip.decompress(path.read_bytes())
    sha = hashlib.sha256(raw).hexdigest()
    if receipt.get("raw_sha256") != sha or receipt.get("stock_code") != code:
        raise ValueError(f"new source raw/receipt SHA differs for {code}")
    if day not in receipt.get("dates", ()):
        raise ValueError(f"source receipt lacks requested day {day}")
    return raw, sha, "not_independently_compared", str(receipt.get("captured_at"))


def _witness(root: Path | None, code: str) -> bytes | None:
    if root is None:
        return None
    path = root / f"{code}.json.gz"
    return gzip.decompress(path.read_bytes()) if path.is_file() else None


def build(
    *,
    day: str,
    market_release: Path,
    source_root: Path,
    source_mode: str,
    witness_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if day not in TARGET or source_mode not in {"archive", "capture"}:
        raise ValueError("unsupported date or source mode")
    if output.exists():
        raise ValueError("never overwrite an immutable research product package")
    policy = json.loads(POLICY_PATH.read_text())
    if (
        policy.get("policy_id") != "quanthus-1m-research-loss-aware-v1"
        or policy.get("product") != PRODUCT
    ):
        raise ValueError("unexpected versioned research quality policy")
    daily, daily_manifest, statuses = _reference(market_release, day)
    ledger = _archived_source_ledger() if source_mode == "archive" else None
    source_identity = source_root / "identity.json"
    if source_mode == "capture":
        identity = json.loads(source_identity.read_text())
        if (
            identity.get("purpose") != "non_publishing_original_source_only"
            or day not in identity.get("dates", [])
            or identity.get("codes_count") != 4992
            or identity.get("source_universe", {}).get("market_date") != "2026-09-24"
        ):
            raise ValueError("capture identity is not the exact declared source product")

    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        schema = pa.schema(
            [
                ("stock_code", pa.string()),
                ("market_date", pa.string()),
                ("source_ts", pa.string()),
                ("exchange_session_phase", pa.string()),
                ("interval_size", pa.string()),
                ("price_basis", pa.string()),
                ("open", pa.decimal128(25, 6)),
                ("high", pa.decimal128(25, 6)),
                ("low", pa.decimal128(25, 6)),
                ("close", pa.decimal128(25, 6)),
                ("volume_shares", pa.decimal128(25, 6)),
                ("amount", pa.decimal128(25, 6)),
                ("raw_sha256", pa.string()),
                ("quality_grade", pa.string()),
                ("original_source_observed", pa.bool_()),
                ("cross_source_status", pa.string()),
            ]
        )
        target = work / "dwd_security_bar_1m_loss_aware.parquet"
        writer = pq.ParquetWriter(target, schema, compression="zstd")
        manifest_dispositions = work / "security-quality.jsonl"
        accepted = Counter()
        reasons = Counter()
        board = defaultdict(lambda: Counter())
        missing_total = 0
        native_rows = 0
        raw_count = 0
        rows_buffer: list[dict[str, Any]] = []
        preview = []
        try:
            with manifest_dispositions.open("x", encoding="ascii") as stream:
                for code in sorted(daily):
                    raw, original_sha, cross_status, acquired = _native_raw(
                        source_root, code, day=day, mode=source_mode, ledger=ledger
                    )
                    if raw is not None:
                        raw_count += 1
                    result: LossAssessment = assess(
                        symbol=code,
                        day=day,
                        raw=raw,
                        shares=daily[code]["volume"],
                        close=daily[code]["close"],
                        traded=statuses[code] != "0",
                        m5_raw=_witness(witness_root, code),
                        cross_source_status=cross_status,
                    )
                    if result.raw_sha256 is not None and result.raw_sha256 != original_sha:
                        raise ValueError(f"source digest is inconsistent for {code}")
                    disp = result.disposition()
                    disp["source_acquired_at_or_after"] = acquired
                    disp["reference_market_release_manifest_sha256"] = daily_manifest
                    disp["source_receipt_root"] = str(source_root)
                    stream.write(_canonical(disp).decode("ascii"))
                    accepted[result.grade] += 1
                    reasons[result.reason] += 1
                    board[_board(code)][result.grade] += 1
                    missing_total += (
                        len(result.missing_stamps) if result.grade != "SUSPENDED" else 0
                    )
                    if len(preview) < 22 and result.grade in {"R2", "R3"}:
                        preview.append(
                            {"symbol": code, "grade": result.grade, "reason": result.reason}
                        )
                    if result.grade in {"R0", "R1"}:
                        for stamp, bar in result.rows:
                            is_auction = stamp.endswith("0930")
                            row = {
                                "stock_code": code,
                                "market_date": day,
                                "source_ts": (
                                    f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} "
                                    f"{stamp[8:10]}:{stamp[10:12]}:00"
                                ),
                                "exchange_session_phase": "auction" if is_auction else "continuous",
                                "interval_size": "1m",
                                "price_basis": "none",
                                "open": bar["open"],
                                "high": bar["high"],
                                "low": bar["low"],
                                "close": bar["close"],
                                "volume_shares": bar["volume"],
                                "amount": None,
                                "raw_sha256": result.raw_sha256,
                                "quality_grade": result.grade,
                                "original_source_observed": True,
                                "cross_source_status": cross_status,
                            }
                            rows_buffer.append(row)
                            native_rows += 1
                            if len(rows_buffer) >= 20000:
                                writer.write_table(pa.Table.from_pylist(rows_buffer, schema=schema))
                                rows_buffer.clear()
                if rows_buffer:
                    writer.write_table(pa.Table.from_pylist(rows_buffer, schema=schema))
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            writer.close()
        eligible = accepted["R0"] + accepted["R1"]
        traded = len(daily) - accepted["SUSPENDED"]
        coverage = Decimal(eligible) / Decimal(traded)
        per_board = {}
        for kind, counts in sorted(board.items()):
            n = sum(counts.values()) - counts["SUSPENDED"]
            if n == 0:
                continue
            covered = counts["R0"] + counts["R1"]
            per_board[kind] = {
                "traded": n,
                "eligible": covered,
                "ratio": str(Decimal(covered) / Decimal(n)),
                "grades": dict(counts),
            }
        market_ready = coverage >= Decimal(
            policy["coverage"]["min_traded_security_fraction"]
        ) and all(
            Decimal(row["ratio"])
            >= Decimal(policy["coverage"]["min_board_traded_security_fraction"])
            for row in per_board.values()
        )
        summary = {
            "schema_version": 1,
            "kind": "quanthus_research_1m_loss_aware_quality_report",
            "product": PRODUCT,
            "market_date": day,
            "data_authority": "P10",
            "consumer": "bounded_non_execution_research_only",
            "source_mode": source_mode,
            "provider": "tencent_mkline_m1",
            "raw_source_root": str(source_root),
            "source_raw_symbols": raw_count,
            "reference_ods_manifest_sha256": daily_manifest,
            "policy_sha256": "sha256:" + _sha(POLICY_PATH),
            "source_clock_semantics": "vendor_native_end_label_no_unproven_cross_vendor_alignment",
            "provider_amount": "absent_not_inferred",
            "pricing_basis": "none_unadjusted",
            "pit_disposition": (
                "historical_source_acquired_after_market_date_not_retroactive_live_knowledge"
            ),
            "original_source_observations_only": True,
            "all_expected_securities": len(daily),
            "traded_denominator": traded,
            "grades": dict(accepted),
            "rejection_reasons": dict(reasons),
            "missing_actual_regular_minutes_including_excluded": missing_total,
            "observed_rows_in_research_dwd": native_rows,
            "traded_eligible": eligible,
            "traded_coverage": str(coverage),
            "per_board": per_board,
            "preview_not_admitted": preview,
            "status": "RESEARCH_READY_LOSS_AWARE"
            if market_ready
            else "PARTIAL_COVERAGE_NO_RESEARCH_READY",
            "legacy_strict_1m_ready": False,
            "existing_daily_core_unchanged": True,
        }
        _write(work / "quality-summary.json", _canonical(summary))
        paths = [p for p in work.iterdir() if p.is_file()]
        inventory = [
            {"path": p.name, "sha256": "sha256:" + _sha(p), "size_bytes": p.stat().st_size}
            for p in sorted(paths)
        ]
        manifest = {
            "schema_version": 1,
            "kind": "quanthus_1m_loss_aware_research_dwd",
            "product_scope": PRODUCT,
            "market_date": day,
            "quality_policy_sha256": "sha256:" + _sha(POLICY_PATH),
            "source_daily_manifest_sha256": daily_manifest,
            "source_identity_sha256": ("sha256:" + _sha(source_identity))
            if source_mode == "capture"
            else None,
            "archived_1m_ledger_sha256": ("sha256:" + _sha(ARCHIVE_LEDGER))
            if source_mode == "archive"
            else None,
            "rows": native_rows,
            "grades": dict(accepted),
            "status": summary["status"],
            "file_inventory": inventory,
            "content_digest": "sha256:" + hashlib.sha256(_canonical(inventory)).hexdigest(),
            "permitted_consumer": "non_execution_gap_aware_development_research_only",
            "forbidden_strict_minute_reuse": True,
        }
        _write(work / "manifest.json", _canonical(manifest))
        if market_ready:
            _write(work / "READY", ("sha256:" + _sha(work / "manifest.json") + "\n").encode())
        else:
            _write(
                work / "NOT_READY",
                _canonical(
                    {
                        "reason": "research_coverage_or_cohort_quality_below_policy_floor",
                        "quality_summary_sha256": "sha256:" + _sha(work / "quality-summary.json"),
                    }
                ),
            )
        os.replace(work, output)
        return summary
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def verify_package(root: Path) -> dict[str, Any]:
    obj = json.loads((root / "manifest.json").read_text())
    ready = (root / "READY").exists()
    if obj.get("product_scope") != PRODUCT or obj.get("status") != (
        "RESEARCH_READY_LOSS_AWARE" if ready else "PARTIAL_COVERAGE_NO_RESEARCH_READY"
    ):
        raise ValueError("product quality READY identity inconsistent")
    if ready and (root / "READY").read_text().strip() != "sha256:" + _sha(root / "manifest.json"):
        raise ValueError("research READY manifest SHA mismatch")
    expected = {"manifest.json", "READY" if ready else "NOT_READY"}
    for x in obj["file_inventory"]:
        p = root / x["path"]
        if (
            p.is_symlink()
            or not p.is_file()
            or p.stat().st_size != x["size_bytes"]
            or "sha256:" + _sha(p) != x["sha256"]
        ):
            raise ValueError("research source-backed payload digest invalid")
        expected.add(x["path"])
    if {p.name for p in root.iterdir()} != expected:
        raise ValueError("undeclared research package members")
    import pyarrow.parquet as pq

    tb = pq.read_table(
        root / "dwd_security_bar_1m_loss_aware.parquet",
        columns=["original_source_observed", "quality_grade", "market_date"],
    )
    if tb.num_rows != obj["rows"] or any(
        not row["original_source_observed"]
        or row["quality_grade"] not in ("R0", "R1")
        or row["market_date"] != obj["market_date"]
        for row in tb.to_pylist()
    ):
        raise ValueError("research bars include non-observed or wrong-date/grade data")
    return {
        "product_scope": PRODUCT,
        "market_date": obj["market_date"],
        "status": obj["status"],
        "rows": tb.num_rows,
        "grades": obj["grades"],
        "manifest_sha256": "sha256:" + _sha(root / "manifest.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-package", type=Path)
    parser.add_argument("--day")
    parser.add_argument("--market-release", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-mode", choices=("archive", "capture"))
    parser.add_argument("--witness-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.verify_package is not None:
        print(json.dumps(verify_package(args.verify_package), sort_keys=True))
        return 0
    if not all((args.day, args.market_release, args.source_root, args.source_mode, args.output)):
        parser.error("day, market-release, source-root, source-mode, output required")
    summary = build(
        day=args.day,
        market_release=args.market_release,
        source_root=args.source_root,
        source_mode=args.source_mode,
        witness_root=args.witness_root,
        output=args.output,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "RESEARCH_READY_LOSS_AWARE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
