"""CN01 P10 ongoing 1m T/T+1 supervisor: calendar-gated and budgeted.

T: actual verified post-close market ODS -> <=50-source capture shards ->
independent source-native quality/DWD; no source-perfect 240 dependency.

T+1: read yesterday's verified market T, sample 12-24 R2/R3 canaries,
compare *dated observation* digests vs immutable T original source.
If unchanged, finish without a second full-market poll. If genuinely changed,
recapture the affected R2/R3 cohort and publish a versioned quality correction.
Never replace old source, product or decision artifacts.

Every atomic step passes the persistent 60m new-operation admission gate.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import verify_ods_release
from quanthus.pipelines.p10_minute_operation_runtime import _atomic, run_one
from quanthus.pipelines.p10_operating_schedule import SHANGHAI, start_at
from quanthus.providers.p10_native_m1_shard_capture import (
    target_date_records,
    verified_daily_universe,
)
from quanthus.providers.trading_calendar import fetch_baostock_calendar

ROOT = Path("/srv/quanthus/dwd-dws-v2/intraday")
DEFAULT_RELEASES = Path("/root/work/astock/data/shadow/cn01-ods-v1/releases/daily")
DEFAULT_JOURNAL = ROOT / "operations/p10-minute"
DEFAULT_CAPTURE = ROOT / "quarantine/p10-minute-live"
DEFAULT_PRODUCT = ROOT / "products/loss-aware-1m-ongoing-v1"
V1_FORENSIC = ROOT / "products/loss-aware-1m-v1"


def calendar_for(date_now: date) -> list[dict[str, str]]:
    first = date_now - timedelta(days=18)
    rows = fetch_baostock_calendar(first, date_now)
    dates = {r["trade_date"] for r in rows}
    if dates != {
        (first + timedelta(days=n)).isoformat()
        for n in range((date_now - first).days + 1)
    }:
        raise ValueError("fresh authoritative trading calendar incomplete")
    return rows


def resolve_target(now: datetime, phase: str, rows: list[dict[str, str]]) -> date | None:
    if now.tzinfo is None:
        raise ValueError("market schedule requires timezone")
    today = now.astimezone(SHANGHAI).date()
    cal = {date.fromisoformat(r["trade_date"]):str(r["is_open"]) for r in rows}
    if phase == "T":
        return today if cal.get(today) == "1" else None
    if phase != "T_plus_1":
        raise ValueError("invalid source capture phase")
    # T+1 source maturity is the next CALENDAR day.  The target T must
    # be an exchange trading day; the recovery/execution day need not be.
    # Thus Friday's source is checked Saturday, not postponed to Monday.
    candidate = today - timedelta(days=1)
    if candidate not in cal:
        raise ValueError("T+1 preceding target day missing from verified calendar")
    return candidate if cal[candidate] == "1" else None


def find_market_ods(releases: Path, target: date) -> Path:
    base = releases / target.isoformat()
    matched = []
    for marker in sorted(base.glob("*/release.json")):
        parent = marker.parent
        try:
            verified = verify_ods_release(parent)
            arts = {a.name for a in verified.release.artifacts}
            if (
                verified.release.market_date == target
                and verified.release.schedule == "daily"
                and {"daily_ohlcv_none_delta", "daily_status_observation_delta"} <= arts
            ):
                matched.append(parent)
        except (ValueError, OSError, KeyError):
            continue
    if len(matched) != 1:
        raise ValueError("missing or ambiguous verified daily market source release")
    return matched[0]


def _phase_lock(journal: Path, target: date, phase: str) -> Path:
    journal.mkdir(parents=True,exist_ok=True)
    return journal / f".{target}-{phase}-supervisor.lock"


def _phase_receipt(journal: Path, target: date, phase: str, value: str) -> None:
    path=journal/f"{target}-{phase}.json"
    if not path.is_file():
        raise ValueError("operation phase has no persisted evidence ledger")
    state=json.loads(path.read_text())
    state["terminal_disposition"]=value
    _atomic(path,state)


def _run(
    *,
    journal: Path, calendar_file: Path, target: date, phase: str,
    operation_id: str, command: list[str], timeout_seconds: int = 600,
) -> dict[str, Any]:
    return run_one(
        state_root=journal, calendar_file=calendar_file,
        market_date=target, phase=phase, operation_id=operation_id,
        argv=command, timeout_seconds=timeout_seconds,
    )


def run_t(
    *,
    target: date, rows: list[dict], journal: Path,
    releases: Path, capture: Path, product: Path,
) -> dict[str, Any]:
    verified=find_market_ods(releases,target)
    codes,manifest=verified_daily_universe(verified,target)
    if not codes or len(codes)!=4992:
        raise ValueError("unqualified T-day population")
    calendar_file=journal / f"{target}-calendar.json"
    _atomic_array(calendar_file,rows)

    stage=capture/target.isoformat()/"T-v1"
    stages=math.ceil(len(codes)/50)
    attempts=[]
    for index in range(stages):
        # Reserve one last bounded 600-second atomic warehouse build.
        # Otherwise a final source shard can consume the entire 60-minute
        # start budget, leaving genuine captured rows unpublished.
        ledger_path = journal / f"{target}-T.json"
        if ledger_path.exists():
            ledger = json.loads(ledger_path.read_text())
            consumed = float(ledger["cumulative_processing_seconds"])
            if consumed >= 3000:
                attempts.append("reserved_budget_for_warehouse_build")
                break
        output=stage/f"shard-{index:03}"
        command=[
            sys.executable,"-m","quanthus.providers.p10_native_m1_shard_capture",
            "--market-date",target.isoformat(),
            "--market-ods-release",str(verified),
            "--output",str(output),
            "--shard-index",str(index),
            "--shard-size","50",
        ]
        result=_run(
            journal=journal, calendar_file=calendar_file,
            target=target, phase="T",
            operation_id=f"capture-{index:03}",command=command,
        )
        attempts.append(result["status"])
        if result["status"]=="cutoff_reached":
            break
    # Product build is a separately admitted atomic operation. If no time
    # remains, keep source evidence and an honest unbuilt disposition.
    have_any=any(stage.glob("shard-*/receipts/*.json"))
    if not have_any:
        _phase_receipt(journal,target,"T","no_original_source_collected")
        return {"status":"capture_unavailable","attempts":attempts}
    cmd=[
        sys.executable,"-m","quanthus.pipelines.p10_minute_live_warehouse",
        "--market-date",target.isoformat(),
        "--market-ods-release",str(verified),
        "--capture-root",str(stage),
        "--product-root",str(product),
    ]
    built=_run(
        journal=journal, calendar_file=calendar_file,
        target=target,phase="T",operation_id="warehouse-build",command=cmd,
    )
    if built["status"] in {"success","already_recorded"}:
        _phase_receipt(journal,target,"T","source_grade_warehouse_published")
    else:
        _phase_receipt(journal,target,"T","partial_source_capture_not_published")
    return {
        "status":built["status"],
        "market_date":target.isoformat(),
        "source_attempts":dict(sorted((k,attempts.count(k)) for k in set(attempts))),
        "publisher":built,
    }


def _atomic_array(path: Path, rows: list[dict]) -> None:
    # A tiny explicit array file, with atomic rename after fsync.
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text()) != rows:
            raise ValueError("calendar attempt changed between shards")
        return
    text=json.dumps(rows,sort_keys=True,separators=(",",":"))+"\n"
    import os
    tmp=path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("x") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _old_original_root(
    target: date, capture: Path, product: Path | None = None,
) -> tuple[Path, dict[str, dict[str, Any]]]:
    # Prefer existing original actual T capture; historic 10/08 uses
    # independently verified immutable original Tencent v1 custody.
    historic=V1_FORENSIC/"date-status"/f"{target.isoformat()}.json"
    if historic.is_file():
        status=json.loads(historic.read_text())
        dwd=V1_FORENSIC/status["research_dwd_path"]/"security-quality.jsonl"
        grades={x["stock_code"]:x for x in (
            json.loads(line) for line in dwd.read_text().splitlines()
        )}
        return V1_FORENSIC/status["source_ods_path"]/"raw",grades
    stage=capture/target.isoformat()/"T-v1"
    if product is None:
        raise ValueError("T+1 requires the sealed T-grade product")
    receipt_dir=product/"date-status"/target.isoformat()
    receipts=sorted(receipt_dir.glob("*.json"),key=lambda x:x.stat().st_mtime_ns)
    if not receipts:
        raise ValueError("T+1 cannot find sealed T-grade receipt")
    receipt=json.loads(receipts[0].read_text())
    from quanthus.pipelines.p10_minute_live_warehouse import verify
    package=product/"packages"/target.isoformat()/receipts[0].stem
    exact=verify(package)
    if (
        exact["market_date"]!=target.isoformat()
        or exact["manifest_sha256"]!=receipt.get("manifest_sha256")
    ):
        raise ValueError("T+1 original grade not bound to sealed manifest")
    grades={row["stock_code"]:row for row in (
        json.loads(line) for line in (package/"quality-status.jsonl").read_text().splitlines()
    )}
    return stage,grades


def _balanced_canaries(
    codes: list[str], grades: dict[str, dict[str, Any]],
    max_count: int=24,
) -> list[str]:
    from quanthus.pipelines.loss_aware_m1_research import _board
    groups:dict[str,list[str]]={}
    for code in codes:
        if grades.get(code,{}).get("quality_grade") in {"R2","R3"}:
            groups.setdefault(_board(code),[]).append(code)
    out=[]
    for board in sorted(groups):
        out.extend(sorted(groups[board])[:max_count//4])
    if len(out)<12:
        for code in codes:
            if code not in out and len(out)<12:
                out.append(code)
    return sorted(out[:max_count])


def _original_for(
    root: Path, code: str,
) -> bytes | None:
    direct=root/f"{code}.json.gz"
    if direct.is_file():
        return gzip.decompress(direct.read_bytes())
    for p in root.glob(f"shard-*/raw/{code}.json.gz"):
        return gzip.decompress(p.read_bytes())
    return None


def _capture_revision_cohort(
    *, target: date, grades: dict[str, dict[str, Any]],
    canaries: list[str], stage: Path, verified: Path, journal: Path,
    calendar_file: Path,
) -> dict[str, Any]:
    """After a true date-bound canary change, poll the affected cohort once.

    Canary receipts are reused, not requested twice. Each bounded shard gets
    its own durable operation id and per-operation timeout. Original T bytes
    remain immutable; the warehouse will overlay genuinely changed sources.
    """
    remaining=sorted(
        code for code,quality in grades.items()
        if quality.get("quality_grade") in {"R2","R3"} and code not in canaries
    )
    selected=stage/"selected-revisions.json"
    _atomic_array(selected,remaining)
    attempted=0
    failed=0
    cutoff=False
    for index in range(math.ceil(len(remaining)/50)):
        ledger_path=journal/f"{target}-T_plus_1.json"
        if ledger_path.is_file() and float(json.loads(
            ledger_path.read_text()
        ).get("cumulative_processing_seconds",0))>=3000:
            cutoff=True
            break  # Reserve final 600 seconds for independent publication.
        result=_run(
            journal=journal,calendar_file=calendar_file,
            target=target,phase="T_plus_1",
            operation_id=f"revision-{index:03}",
            command=[
                sys.executable,"-m","quanthus.providers.p10_native_m1_shard_capture",
                "--market-date",target.isoformat(),
                "--market-ods-release",str(verified),
                "--output",str(stage/f"shard-{index+100:03}"),
                "--shard-index",str(index),"--shard-size","50",
                "--codes-file",str(selected),
            ],
        )
        if result["status"]=="cutoff_reached":
            cutoff=True
            break
        attempted+=1
        if result["status"] not in {"success","already_recorded"}:
            failed+=1
    return {
        "target_count":len(remaining),"shards_attempted":attempted,
        "failed_shards":failed,"cutoff_reached":cutoff,
    }


def run_tplus1(
    *,
    target: date, rows: list[dict], journal: Path,
    releases: Path, capture: Path, product: Path,
) -> dict[str, Any]:
    verified=find_market_ods(releases,target)
    codes,_=verified_daily_universe(verified,target)
    old_root,grades=_old_original_root(target,capture,product)
    canaries=_balanced_canaries(codes,grades)
    if len(canaries)<12 or len(canaries)>24:
        raise ValueError("T+1 source maturity canaries inadequate")
    calendar_file=journal/f"{target}-tplus1-calendar.json"
    _atomic_array(calendar_file,rows)
    stage=capture/target.isoformat()/"Tplus1-v1"
    stage.mkdir(parents=True,exist_ok=True)
    selection=stage/"selected-canaries.json"
    if selection.exists():
        if json.loads(selection.read_text())!=canaries:
            raise ValueError("maturity canary cohort not reproducible")
    else:
        _atomic_array(selection,canaries)
    result=_run(
        journal=journal,calendar_file=calendar_file,
        target=target,phase="T_plus_1",
        operation_id="native-source-maturity-canary",
        command=[
            sys.executable,"-m","quanthus.providers.p10_native_m1_shard_capture",
            "--market-date",target.isoformat(),
            "--market-ods-release",str(verified),
            "--output",str(stage/"shard-000"),
            "--shard-index","0","--shard-size","50",
            "--codes-file",str(selection),
        ],
    )
    changed=[]
    unchanged=[]
    unavailable=[]
    for code in canaries:
        current=stage/"shard-000"/"raw"/f"{code}.json.gz"
        before=_original_for(old_root,code)
        if not current.is_file() or before is None:
            unavailable.append(code)
            continue
        original_hash=target_date_records(before,code,target)[1]
        observed_hash=target_date_records(
            gzip.decompress(current.read_bytes()),code,target
        )[1]
        (changed if original_hash!=observed_hash else unchanged).append(code)
    correction = None
    revision_cohort = None
    if result["status"] not in {"success", "already_recorded"} or unavailable:
        disposition="bounded_maturity_probe_incomplete"
    elif changed and target < date(2026, 10, 9):
        # Historical 10/08 remains bound to its sealed V2/forensic identity.
        # A newly observed source requires a separately versioned #502 review.
        disposition="historical_source_change_requires_separate_quality_authority"
    elif changed:
        revision_cohort=_capture_revision_cohort(
            target=target,grades=grades,canaries=canaries,
            stage=stage,verified=verified,journal=journal,
            calendar_file=calendar_file,
        )
        correction = _run(
            journal=journal,
            calendar_file=calendar_file,
            target=target,
            phase="T_plus_1",
            operation_id="warehouse-immutable-correction",
            command=[
                sys.executable,
                "-m", "quanthus.pipelines.p10_minute_live_warehouse",
                "--market-date",target.isoformat(),
                "--market-ods-release",str(verified),
                "--capture-root",str(stage),
                "--prior-capture-root",str(capture/target.isoformat()/"T-v1"),
                "--product-root",str(product),
            ],
        )
        disposition=(
            "grade_qualified"
            if correction["status"] in {"success", "already_recorded"}
            else "source_changed_requires_new_versioned_quality_requalification"
        )
    else:
        disposition="source_unchanged_stopped"
    _phase_receipt(journal,target,"T_plus_1",disposition)
    return {
        "status":disposition,
        "market_date":target.isoformat(),
        "T_plus_1_phase":result["status"],
        "changed_count":len(changed),"unchanged_count":len(unchanged),
        "unavailable_count":len(unavailable),"changed_codes":changed,
        "source_revisions_only_not_grade_promotion":correction is None,
        "revision_cohort":revision_cohort,
        "correction":correction,
    }


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",choices=("T","T_plus_1"),required=True)
    parser.add_argument("--releases",type=Path,default=DEFAULT_RELEASES)
    parser.add_argument("--journal",type=Path,default=DEFAULT_JOURNAL)
    parser.add_argument("--capture",type=Path,default=DEFAULT_CAPTURE)
    parser.add_argument("--product",type=Path,default=DEFAULT_PRODUCT)
    parser.add_argument("--dry-run",action="store_true")
    opts=parser.parse_args()
    now=datetime.now(SHANGHAI)
    rows=calendar_for(now.date())
    target=resolve_target(now,opts.phase,rows)
    if target is None:
        print(json.dumps({"status":"verified_nontrading_day_skip","phase":opts.phase}))
        return 0
    planned=start_at(target, "minute_T" if opts.phase=="T" else "minute_Tplus1",rows)
    if now<planned:
        print(json.dumps({"status":"before_authorized_start","planned":planned.isoformat()}))
        return 0
    if opts.dry_run:
        print(json.dumps({
            "status":"planning_only","target":target.isoformat(),
            "planned":planned.isoformat(),"phase":opts.phase,
        }))
        return 0
    with _phase_lock(opts.journal,target,opts.phase).open("a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        if opts.phase=="T":
            result=run_t(
                target=target,rows=rows,journal=opts.journal,
                releases=opts.releases,capture=opts.capture,product=opts.product,
            )
        else:
            result=run_tplus1(
                target=target,rows=rows,journal=opts.journal,
                releases=opts.releases,capture=opts.capture,
                product=opts.product,
            )
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
