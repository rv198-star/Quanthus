"""P10 source-native ongoing 1m warehouse: store R2, quarantine R3.

This is an independent future-date successor to immutable five-date V2.
No inferred OHLC, residual shares or single-vendor implicit adjustment.
R0 is exact full source; R1 requires separately verified same-vendor 5m;
without such a witness, incomplete but daily-consistent native source is R2.
Only default R0/R1 research consumers may read the product READY.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.pipelines.loss_aware_m1_research import _board, _reference
from quanthus.providers.p10_native_m1_shard_capture import target_date_records
from quanthus.providers.tencent_m1_normalize import expected_times

KIND = "p10_loss_aware_1m_live_source_grade_v1"
PRODUCT = "intraday-1m-loss-aware-ongoing-v1"
SOURCE_KIND = "p10_tencent_native_m1_raw_capture_shard"
SIX = re.compile(r"[0-9]{6}\Z")
ONE = Decimal("1")
ZERO = Decimal("0")


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _encode(value: Any) -> bytes:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return (encoded + "\n").encode()


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("source price/volume is not Decimal") from exc
    if not number.is_finite():
        raise ValueError("nonfinite source value")
    return number


def native_minutes(
    original: bytes, code: str, day: str
) -> dict[str, dict[str, Decimal]]:
    """Only genuinely observed m1 bars for target day; no timestamps shifted."""
    if not SIX.fullmatch(code) or date.fromisoformat(day) < date(2026, 10, 9):
        raise ValueError("dynamic capture is outside successor market scope")
    doc = json.loads(original)
    alias = ("sh" if code.startswith("6") else "sz") + code
    if doc.get("code") != 0:
        raise ValueError("Tencent upstream reported failure")
    data = doc.get("data", {}).get(alias, {}).get("m1")
    if not isinstance(data, list):
        raise ValueError("native minute observations missing")
    grid = set(expected_times(day))
    permitted = grid | {day.replace("-", "") + "0930"}
    multiplier = ONE if code.startswith(("688", "689")) else Decimal(100)
    out: dict[str, dict[str, Decimal]] = {}
    for item in data:
        if not isinstance(item, list) or len(item) < 7:
            raise ValueError("invalid provider minute record")
        stamp = str(item[0])
        if not stamp.startswith(day.replace("-", "")):
            continue
        if stamp in out or stamp not in permitted or item[6] not in ({}, None):
            raise ValueError("duplicate/out-of-session/malformed source 1m")
        op,cl,hi,lo,volume=(_decimal(item[i]) for i in (1,2,3,4,5))
        if hi < max(op,cl,lo) or lo > min(op,cl,hi) or volume < ZERO:
            raise ValueError("source 1m OHLC or shares invalid")
        out[stamp] = {
            "open":op, "close":cl, "high":hi, "low":lo,
            "volume_shares":volume*multiplier,
        }
    return out


def assess_native(
    *,
    code: str, day: str, raw: bytes | None,
    traded: bool, daily_shares: Any, daily_close: Any,
) -> tuple[dict[str, Any], dict[str, dict[str, Decimal]]]:
    grid = expected_times(day)
    state = {
        "stock_code":code, "market_date":day, "quality_grade":"R3",
        "quality_reason":"original_source_unavailable", "missing_actual_minutes":list(grid),
        "critical_gap":True, "raw_sha256":_sha(raw) if raw else None,
        "source_acquired_at_or_after":None, "auction_source_observed":False,
        "formally_ingested":False, "default_research_admitted":False,
        "allowed_consumer":"none", "source_semantic_disagreement":"unknown",
        "daily_share_delta":None, "daily_close_delta":None,
        "strict_240_source_qualified":False,
    }
    if not traded:
        if _decimal(daily_shares) == ZERO:
            state.update(
                quality_grade="SUSPENDED",quality_reason="sealed_daily_no_trade",
                missing_actual_minutes=[],critical_gap=False,
            )
        else:
            state["quality_reason"]="suspended_with_positive_daily_volume"
        return state, {}
    if raw is None:
        return state, {}
    try:
        bars = native_minutes(raw,code,day)
        if not bars:
            state["quality_reason"]="no_real_target_date_m1_bars"
            return state, {}
        missing=[stamp for stamp in grid if stamp not in bars]
        critical=set((*grid[:5],*grid[115:120],*grid[120:125],*grid[-5:]))
        total=sum((x["volume_shares"] for x in bars.values()),ZERO)
        expected=_decimal(daily_shares)
        end=bars.get(grid[-1])
        state.update(
            missing_actual_minutes=missing,
            critical_gap=bool(critical.intersection(missing)),
            auction_source_observed=(day.replace("-","")+"0930") in bars,
            daily_share_delta=str(total-expected),
            daily_close_delta=(str(end["close"]-_decimal(daily_close)) if end else None),
        )
        if end is None or end["close"].quantize(Decimal(".01")) != _decimal(
            daily_close
        ).quantize(Decimal(".01")):
            state["quality_reason"]="missing_or_wrong_terminal_close"
            return state, {}
        if total != expected:
            state["quality_reason"]="minute_daily_volume_mismatch"
            return state, {}
        if not missing and state["auction_source_observed"]:
            grade,reason="R0","native_240_and_auction_observed_exact_daily"
        else:
            # Genuine native rows, complete daily shares and close. Without
            # 5m bucket proof this is NOT R1 and cannot be used by the default
            # minute research consumer.
            grade,reason="R2","native_incomplete_or_unwitnessed_restricted"
        state.update(
            quality_grade=grade,
            quality_reason=reason,
            formally_ingested=True,
            default_research_admitted=(grade=="R0"),
            allowed_consumer=(
                "gap_aware_non_execution_development" if grade=="R0"
                else "explicit_r2_review_only"
            ),
        )
        return state,bars
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        state.update(quality_reason=f"invalid_native_source:{type(exc).__name__}")
        return state,{}


def _source_index(
    *, capture_root: Path, day: str, daily_sha: str, securities: set[str],
) -> dict[str, tuple[Path, dict[str, Any]]]:
    seen: dict[str, tuple[Path, dict[str, Any]]] = {}
    for shard in sorted(capture_root.glob("shard-*")):
        identity=shard/"identity.json"
        if not identity.is_file():
            raise ValueError("published capture shard lacks source identity")
        meta=json.loads(identity.read_text())
        if (
            meta.get("kind")!=SOURCE_KIND
            or meta.get("market_date")!=day
            or meta.get("market_ods_manifest_sha256")!=daily_sha
        ):
            raise ValueError("capture shard source/date/daily manifest differs")
        for code in meta["selected_symbols"]:
            if code not in securities or code in seen:
                raise ValueError("source capture duplicates or outside daily universe")
            receipt=shard/"receipts"/f"{code}.json"
            if receipt.is_file():
                obj=json.loads(receipt.read_text())
                if (
                    obj.get("stock_code")!=code
                    or obj.get("market_date")!=day
                    or obj.get("status")!="raw_saved"
                    or obj.get("source_market_ods_sha256")!=daily_sha
                ):
                    raise ValueError("source receipt identity/qualification mismatch")
                seen[code]=(shard/"raw"/f"{code}.json.gz",obj)
            else:
                seen[code]=(Path("/nonexistent-p10-original-source"),{})
    return seen


def _inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path":p.relative_to(root).as_posix(),
            "bytes":p.stat().st_size,
            "sha256":_sha(p.read_bytes()),
        }
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in {"manifest.json","SEALED"}
    ]


def build(
    *, day: str, daily_release: Path, capture_root: Path, product_root: Path,
    prior_capture_root: Path | None = None,
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if date.fromisoformat(day) < date(2026,10,9):
        raise ValueError("live minute product cannot rewrite frozen historical V2")
    market,daily_sha,statuses=_reference(daily_release,day)
    index=_source_index(
        capture_root=capture_root, day=day, daily_sha=daily_sha,
        securities=set(market),
    )
    changed_source_codes: list[str] = []
    if prior_capture_root is not None:
        # T+1 is a *correction*, not a replacement of sealed T originals.
        # Overlay only newly observed genuine target-date revisions. A rolling
        # HTTP SHA difference outside that date is never correction evidence.
        prior_index = _source_index(
            capture_root=prior_capture_root, day=day, daily_sha=daily_sha,
            securities=set(market),
        )
        merged = dict(prior_index)
        for code, (path, receipt) in index.items():
            if not receipt:
                continue
            old = prior_index.get(code)
            if old is not None and old[1]:
                before_raw = gzip.decompress(old[0].read_bytes())
                after_raw = gzip.decompress(path.read_bytes())
                before_digest = target_date_records(
                    before_raw, code, date.fromisoformat(day)
                )[1]
                after_digest = target_date_records(
                    after_raw, code, date.fromisoformat(day)
                )[1]
                if before_digest == after_digest:
                    continue
            merged[code] = (path, receipt)
            changed_source_codes.append(code)
        index = merged
    # An immutable product may record partial source supply; missing securities
    # become explicit R3 rejection metadata, not fabricated minute rows.
    candidate_digest=_sha(_encode([
        day,daily_sha,
        sorted((code, row.get("raw_sha256"))
            for code,(_,row) in index.items())
    ]))[7:23]
    dest=product_root/"packages"/day/candidate_digest
    if dest.exists():
        return verify(dest)
    dest.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=".p10-1m-build-",dir=dest.parent))
    try:
        (stage/"ods"/"raw").mkdir(parents=True)
        schema=pa.schema([
            ("stock_code",pa.string()),("market_date",pa.string()),
            ("source_ts",pa.string()),("exchange_session_phase",pa.string()),
            ("open",pa.decimal128(25,6)),("high",pa.decimal128(25,6)),
            ("low",pa.decimal128(25,6)),("close",pa.decimal128(25,6)),
            ("volume_shares",pa.decimal128(25,6)),("amount",pa.decimal128(25,6)),
            ("raw_sha256",pa.string()),("quality_grade",pa.string()),
            ("quality_reason",pa.string()),("original_source_observed",pa.bool_()),
            ("default_research_admitted",pa.bool_()),
        ])
        writer=pq.ParquetWriter(stage/"minute-real-observed.parquet",schema,compression="zstd")
        grades=Counter()
        board=defaultdict(Counter)
        rows=Counter()
        buffer=[]
        acquired_count=0
        try:
            with (stage/"quality-status.jsonl").open("xb") as output:
                for code in sorted(market):
                    raw=None
                    acquired=None
                    if code in index:
                        path,receipt=index[code]
                        if receipt:
                            raw=gzip.decompress(path.read_bytes())
                            if receipt["raw_sha256"]!=hashlib.sha256(raw).hexdigest():
                                raise ValueError("source original digest no longer matches receipt")
                            if (
                                target_date_records(raw,code,date.fromisoformat(day))[1]
                                != receipt["target_date_observation_sha256"]
                            ):
                                raise ValueError("source target-date digest differs from receipt")
                            acquired=receipt["captured_at"]
                    quality,bars=assess_native(
                        code=code,day=day,raw=raw,
                        daily_shares=market[code]["volume"],
                        daily_close=market[code]["close"],
                        traded=statuses[code]!="0",
                    )
                    quality["source_acquired_at_or_after"]=acquired
                    quality["reference_ods_manifest_sha256"]=daily_sha
                    output.write(_encode(quality))
                    grades[quality["quality_grade"]]+=1
                    board[_board(code)][quality["quality_grade"]]+=1
                    if quality["formally_ingested"]:
                        # Copy authentic response into *formal* source ODS only
                        # for R0/R1/R2; rejected original source stays outside.
                        if raw is None:
                            raise ValueError("admitted original source bytes unexpectedly absent")
                        copied=stage/"ods"/"raw"/f"{code}.json.gz"
                        shutil.copyfile(index[code][0],copied)
                        acquired_count+=1
                        for stamp,bar in sorted(bars.items()):
                            buffer.append({
                                "stock_code":code,"market_date":day,
                                "source_ts":(
                                    f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} "
                                    f"{stamp[8:10]}:{stamp[10:12]}:00"
                                ),
                                "exchange_session_phase":(
                                    "auction" if stamp.endswith("0930") else "continuous"
                                ),
                                **bar,"amount":None,
                                "raw_sha256":quality["raw_sha256"],
                                "quality_grade":quality["quality_grade"],
                                "quality_reason":quality["quality_reason"],
                                "original_source_observed":True,
                                "default_research_admitted":quality["default_research_admitted"],
                            })
                            rows[quality["quality_grade"]]+=1
                            if len(buffer)>=20000:
                                writer.write_table(pa.Table.from_pylist(buffer,schema=schema))
                                buffer.clear()
                if buffer:
                    writer.write_table(pa.Table.from_pylist(buffer,schema=schema))
                output.flush()
                os.fsync(output.fileno())
        finally:
            writer.close()
        traded=sum(grades.values())-grades["SUSPENDED"]
        coverage=Decimal(grades["R0"]+grades["R1"])/Decimal(traded)
        cohort=all(
            Decimal(k["R0"]+k["R1"])/Decimal(
                sum(k.values())-k["SUSPENDED"]
            )>=Decimal("0.98")
            for k in board.values()
            if sum(k.values())>k["SUSPENDED"]
        )
        ready=coverage>=Decimal("0.99") and cohort
        summary={
            "kind":KIND,"market_date":day,
            "product_scope":PRODUCT,"grades":dict(grades),
            "rows_per_grade":dict(rows),"research_ready":bool(ready),
            "admitted_raw":acquired_count,"R3_formal_originals":0,
            "source_grade_coverage":str(coverage),
            "source_ods_manifest_sha256":daily_sha,
            "quality_limits":"R2 storage only; default consumer requires R0/R1",
            "source_capture_root":str(capture_root),
            "prior_source_capture_root":(
                str(prior_capture_root) if prior_capture_root is not None else None
            ),
            "date_slice_revised_securities":changed_source_codes,
            "pit":"original_actual_acquisition_time_not_retroactive_as_known",
        }
        _write(stage/"quality-summary.json",_encode(summary))
        manifest={
            **summary,
            "files":_inventory(stage),
            "sealed_at":datetime.now(timezone.utc).isoformat(),
        }
        _write(stage/"manifest.json",_encode(manifest))
        _write(stage/"SEALED",(_sha((stage/"manifest.json").read_bytes())+"\n").encode())
        verify(stage)
        os.replace(stage,dest)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    result=verify(dest)
    # Full DWD readiness and actual source storage are separate.
    state=product_root/"date-status"/day
    state.mkdir(parents=True,exist_ok=True)
    receipt=state/f"{candidate_digest}.json"
    _write(receipt,_encode(result))
    return result


def verify(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq
    manifest_path=root/"manifest.json"
    manifest=json.loads(manifest_path.read_text())
    if manifest.get("kind")!=KIND or manifest.get("product_scope")!=PRODUCT:
        raise ValueError("wrong product scope/lineage")
    if (root/"SEALED").read_text().strip()!=_sha(manifest_path.read_bytes()):
        raise ValueError("package seal SHA differs")
    expected={"manifest.json","SEALED"}
    for item in manifest["files"]:
        rel=item["path"]
        p=root/rel
        if (
            p.is_symlink() or not p.resolve().is_relative_to(root.resolve())
            or not p.is_file() or p.stat().st_size!=item["bytes"]
            or _sha(p.read_bytes())!=item["sha256"]
        ):
            raise ValueError("input or parquet file checksum changed")
        expected.add(rel)
    actual={p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if expected!=actual:
        raise ValueError("undeclared original source or payload")
    statuses={}
    for line in (root/"quality-status.jsonl").read_text().splitlines():
        x=json.loads(line)
        if x["stock_code"] in statuses:
            raise ValueError("duplicated original security")
        statuses[x["stock_code"]]=x
    if len(statuses)!=4992:
        raise ValueError("date-security population incomplete")
    grade_rows=Counter()
    r2_with_rows=Counter()
    file=pq.ParquetFile(root/"minute-real-observed.parquet")
    for batch in file.iter_batches(batch_size=100000):
        for row in batch.to_pylist():
            quality=statuses.get(row["stock_code"])
            if (
                quality is None
                or row["market_date"]!=manifest["market_date"]
                or row["quality_grade"] not in {"R0","R1","R2"}
                or row["quality_grade"]!=quality["quality_grade"]
                or not row["original_source_observed"]
                or row["raw_sha256"]!=quality["raw_sha256"]
                or row["default_research_admitted"]!=quality["default_research_admitted"]
            ):
                raise ValueError("source grade/physical DWD row mismatch")
            grade_rows[row["quality_grade"]]+=1
            if row["quality_grade"]=="R2":
                r2_with_rows[row["stock_code"]]+=1
    for code,x in statuses.items():
        raw=(root/"ods"/"raw"/f"{code}.json.gz")
        if x["quality_grade"] in {"R0","R1","R2"}:
            if not raw.is_file() or raw.is_symlink() or _sha(gzip.decompress(
                raw.read_bytes()
            ))!=x["raw_sha256"]:
                raise ValueError("admitted original gzip source differs")
            if x["quality_grade"]=="R2" and not r2_with_rows[code]:
                raise ValueError("R2 source exists but its actual bars absent")
        elif raw.exists():
            raise ValueError("R3 or suspended raw entered formal warehouse")
    if dict(grade_rows)!=manifest["rows_per_grade"]:
        raise ValueError("Parquet observed-source grade row count differs")
    return {
        "market_date":manifest["market_date"],
        "product_scope":PRODUCT,
        "manifest_sha256":_sha(manifest_path.read_bytes()),
        "grade_rows":dict(grade_rows),
        "quality_grades":manifest["grades"],
        "research_ready":manifest["research_ready"],
        "admitted_raw":manifest["admitted_raw"],
        "R3_rejected":manifest["grades"].get("R3",0),
    }


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-date")
    parser.add_argument("--market-ods-release",type=Path)
    parser.add_argument("--capture-root",type=Path)
    parser.add_argument("--prior-capture-root",type=Path)
    parser.add_argument("--product-root",type=Path)
    parser.add_argument("--verify-package",type=Path)
    args=parser.parse_args()
    if args.verify_package:
        print(json.dumps(verify(args.verify_package),sort_keys=True))
        return 0
    if not all((args.market_date,args.market_ods_release,args.capture_root,args.product_root)):
        parser.error("market-date, market-ods-release, capture-root, product-root required")
    print(json.dumps(build(
        day=args.market_date,daily_release=args.market_ods_release,
        capture_root=args.capture_root,product_root=args.product_root,
        prior_capture_root=args.prior_capture_root,
    ),sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
