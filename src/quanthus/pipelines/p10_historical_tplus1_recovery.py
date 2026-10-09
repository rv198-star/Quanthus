"""Owner-authorized 2026-10-08 historical T+1 completion (one-off).

Never edit the 10/08 T/T+1 original ledger or sealed V1/V2 products.
Shares the original 60m work budget via SHA-bound inherited cumulative seconds.
Each 50-security source shard is a 600s atomic operation with its own receipt.
A successful source maturity canary is required before expensive remediation.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_minute_daily_cycle import (
    DEFAULT_CAPTURE,
    DEFAULT_JOURNAL,
    DEFAULT_RELEASES,
    V1_FORENSIC,
    _atomic_array,
    _balanced_canaries,
    _old_original_root,
    _original_for,
    find_market_ods,
)
from quanthus.pipelines.p10_minute_operation_runtime import _atomic
from quanthus.providers.p10_native_m1_shard_capture import (
    target_date_records,
    verified_daily_universe,
)

DAY = date(2026, 10, 8)
PRODUCT = Path("/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-historical-correction-v1")
REPAIR_POLICY = "p10-owner-authorized-20261008-historical-tplus1-recovery-v1"
HARD_CUTOFF = 3600
PRE_PUBLICATION_RESERVE = 600


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _run_operation(
    *, state_path: Path, state: dict[str, Any],
    operation_id: str, command: list[str],
    allow_publication: bool = False,
) -> dict[str, Any]:
    digest=_sha(json.dumps(command,separators=(",",":")).encode())
    previous=next(
        (op for op in state["operations"] if op["operation_id"]==operation_id),
        None,
    )
    if previous is not None:
        if previous["command_digest"]!=digest:
            raise ValueError("recovery operation identity reused with different command")
        return previous
    if state.get("inflight_operation"):
        raise ValueError("unreconciled in-flight recovery operation")
    elapsed=float(state["cumulative_processing_seconds"])
    # Reserve the publisher for the end rather than spending all 60m on HTTP.
    if elapsed >= (HARD_CUTOFF if allow_publication else HARD_CUTOFF-PRE_PUBLICATION_RESERVE):
        return {"operation_id":operation_id,"outcome":"cutoff_reached"}
    record={
        "operation_id":operation_id,"command_digest":digest,
        "started_at":datetime.now(timezone.utc).isoformat(),
        "admitted_at_cumulative_seconds":elapsed,
        "timeout_seconds":600,
    }
    state["inflight_operation"]=record
    _atomic(state_path,state)
    log=state_path.parent/f"{operation_id}.log"
    started=time.monotonic()
    rc=127
    timed_out=False
    with log.open("xb") as output:
        process=subprocess.Popen(
            command,stdin=subprocess.DEVNULL,stdout=output,
            stderr=subprocess.STDOUT,start_new_session=True,
        )
        try:
            rc=process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            timed_out=True
            os.killpg(process.pid,signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait()
            rc=124
    duration=time.monotonic()-started
    result={
        **record,"finished_at":datetime.now(timezone.utc).isoformat(),
        "duration_seconds":round(duration,3),"exit_code":rc,
        "outcome":"success" if rc==0 else "failed",
        "timed_out_by_own_operation_limit":timed_out,
        "log_path":str(log),
        "completed_after_cutoff":elapsed+duration>=HARD_CUTOFF,
    }
    state["cumulative_processing_seconds"]+=duration
    state["inflight_operation"]=None
    state["operations"].append(result)
    _atomic(state_path,state)
    return result


def run(*, original_journal: Path, captures: Path, releases: Path, forensic: Path,
        product: Path, recovery_journal: Path) -> dict[str, Any]:
    original_path=original_journal/f"{DAY}-T_plus_1.json"
    frozen_bytes=original_path.read_bytes()
    old=json.loads(frozen_bytes)
    if (
        old.get("market_date")!=DAY.isoformat()
        or old.get("phase")!="T_plus_1"
        or old.get("terminal_disposition")
        !="historical_source_change_requires_separate_quality_authority"
        or old.get("inflight_operation") is not None
    ):
        raise ValueError("historical recovery requires completed 10/08 canary")
    verified=find_market_ods(releases,DAY)
    codes,_=verified_daily_universe(verified,DAY)
    old_root,grades=_old_original_root(DAY,captures)
    canaries=_balanced_canaries(codes,grades)
    stage=captures/DAY.isoformat()/"Tplus1-v1"
    recorded=(stage/"selected-canaries.json")
    if not recorded.is_file() or json.loads(recorded.read_text())!=canaries:
        raise ValueError("historical frozen canary cohort changed")
    changed=[]
    for code in canaries:
        current=stage/"shard-000"/"raw"/f"{code}.json.gz"
        prior=_original_for(old_root,code)
        if prior is None or not current.is_file():
            raise ValueError("historical canary original/revision missing")
        import gzip
        if target_date_records(prior,code,DAY)[1]!=target_date_records(
            gzip.decompress(current.read_bytes()),code,DAY
        )[1]:
            changed.append(code)
    if not changed:
        raise ValueError("recovery is not supported without real dated source revision")
    selected=sorted(
        code for code,x in grades.items()
        if x["quality_grade"] in {"R2","R3"} and code not in canaries
    )
    selected_path=stage/"selected-revisions.json"
    _atomic_array(selected_path,selected)
    recovery_journal.mkdir(parents=True,exist_ok=True)
    state_path=recovery_journal/f"{DAY}-owner-recovery-v1.json"
    with (recovery_journal/f".{DAY}.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        if state_path.exists():
            state=json.loads(state_path.read_text())
            if (
                state.get("policy_id")!=REPAIR_POLICY
                or state.get("original_ledger_sha256")!=_sha(frozen_bytes)
                or state.get("baseline_cumulative_seconds")!=old["cumulative_processing_seconds"]
            ):
                raise ValueError("historical recovery ledger is not bound to frozen source")
        else:
            state={
                "policy_id":REPAIR_POLICY,"market_date":DAY.isoformat(),
                "original_ledger_sha256":_sha(frozen_bytes),
                "baseline_cumulative_seconds":old["cumulative_processing_seconds"],
                "cumulative_processing_seconds":float(old["cumulative_processing_seconds"]),
                "operations":[],"inflight_operation":None,
                "target_count":len(selected),"canary_changed_count":len(changed),
            }
            _atomic(state_path,state)
        results=[]
        for index in range(math.ceil(len(selected)/50)):
            op=f"historical-revision-{index:03}"
            command=[
                sys.executable,"-m","quanthus.providers.p10_native_m1_shard_capture",
                "--market-date",DAY.isoformat(),
                "--market-ods-release",str(verified),
                "--output",str(stage/f"shard-{index+100:03}"),
                "--shard-index",str(index),"--shard-size","50",
                "--codes-file",str(selected_path),
            ]
            outcome=_run_operation(
                state_path=state_path,state=state,operation_id=op,
                command=command,
            )
            results.append(outcome["outcome"])
            if outcome["outcome"]=="cutoff_reached":
                break
        # This is a separate corrected data product.  Original forensic
        # evidence and the V2 date-status receipts remain unmodified.
        publish=_run_operation(
            state_path=state_path,state=state,
            operation_id="historical-versioned-warehouse-publish",
            command=[
                sys.executable,"-m","quanthus.pipelines.p10_minute_live_warehouse",
                "--market-date",DAY.isoformat(),
                "--market-ods-release",str(verified),
                "--capture-root",str(stage),
                "--historical-forensic-root",str(forensic),
                "--product-root",str(product),
            ],
            allow_publication=True,
        )
        status={
            "market_date":DAY.isoformat(),"original_ledger_sha256":_sha(frozen_bytes),
            "canary_changed_count":len(changed),"target_corrections":len(selected),
            "operations":dict((x,results.count(x)) for x in set(results)),
            "publication":publish,
            "effective_cumulative_seconds":state["cumulative_processing_seconds"],
            "unchanged_frozen_evidence":original_path.read_bytes()==frozen_bytes,
        }
        if not status["unchanged_frozen_evidence"]:
            raise ValueError("historical original ledger changed")
        return status


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-date",required=True)
    parser.add_argument("--owner-authorized-one-off",action="store_true")
    parser.add_argument("--original-journal",type=Path,default=DEFAULT_JOURNAL)
    parser.add_argument("--capture",type=Path,default=DEFAULT_CAPTURE)
    parser.add_argument("--releases",type=Path,default=DEFAULT_RELEASES)
    parser.add_argument("--forensic",type=Path,default=V1_FORENSIC)
    parser.add_argument("--product",type=Path,default=PRODUCT)
    parser.add_argument("--recovery-journal",type=Path,default=DEFAULT_JOURNAL/"recovery-20261010")
    args=parser.parse_args()
    if args.market_date!=DAY.isoformat() or not args.owner_authorized_one_off:
        parser.error("requires explicit authorized 2026-10-08 one-off recovery")
    print(json.dumps(run(
        original_journal=args.original_journal,
        captures=args.capture,
        releases=args.releases,
        forensic=args.forensic,
        product=args.product,
        recovery_journal=args.recovery_journal,
    ),sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
