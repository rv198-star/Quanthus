"""Strict PASS/FAIL minute-delivery evidence for CN01 T and calendar-day T+1.

A truthful minute *delivery* PASS means the immutable source-grade product
contains all traded securities in R0/R1/R2, with R3=0, verified physical
source/Parquet/manifest custody and a completed phase. It does NOT certify
the stricter R0/R1-only Research READY consumer qualification.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_deadline_notifications import _bounded_inflight
from quanthus.pipelines.p10_minute_live_warehouse import verify
from quanthus.pipelines.p10_operating_schedule import (
    alert_checkpoint,
)

PHASES = {"minute_T": "T", "minute_Tplus1": "T_plus_1"}
ACCEPTED_TERMINALS = {
    "minute_T": {"source_grade_warehouse_published"},
    "minute_Tplus1": {"grade_qualified", "source_unchanged_stopped"},
}
GRADE_KEYS = ("R0", "R1", "R2", "R3", "SUSPENDED")


def verified_minute_grade(root: Path, target: date) -> dict[str, Any]:
    """Reject bare flags and receipt-only products; verify actual R2 storage."""
    day = target.isoformat()
    receipts = root / "date-status" / day
    candidates = sorted(
        receipts.glob("*.json"),
        key=lambda file: (file.stat().st_mtime_ns, file.name),
        reverse=True,
    )
    if not candidates:
        raise ValueError("minute_sealed_grade_receipt_missing")
    receipt_file = candidates[0]
    receipt = json.loads(receipt_file.read_text())
    if receipt.get("market_date") != day:
        raise ValueError("minute_grade_receipt_date_mismatch")
    manifest_sha = receipt.get("manifest_sha256")
    if not isinstance(manifest_sha, str) or len(manifest_sha) != 71:
        raise ValueError("minute_grade_receipt_manifest_sha_missing")
    package = root / "packages" / day / receipt_file.stem
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("minute_grade_manifest_missing")
    if "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_sha:
        raise ValueError("minute_grade_receipt_manifest_sha_mismatch")
    # verify() validates SEALED, complete inventory SHA, date-bound 4,992
    # security-quality statuses, R2 original bytes and physical Parquet, and
    # absence of any R3 original bytes in the formal ODS.
    verified = verify(package)
    if verified["manifest_sha256"] != manifest_sha or verified["market_date"] != day:
        raise ValueError("minute_grade_physical_verification_failed")
    grades = verified["quality_grades"]
    if (
        not isinstance(grades, dict)
        or any(type(grades.get(g, 0)) is not int or grades.get(g, 0) < 0 for g in GRADE_KEYS)
        or sum(grades.get(g, 0) for g in GRADE_KEYS) != 4992
        or any(key not in GRADE_KEYS for key in grades)
        or receipt.get("quality_grades") != grades
        or type(receipt.get("research_ready")) is not bool
        or receipt.get("research_ready") is not verified["research_ready"]
        or verified["admitted_raw"] != sum(grades.get(g, 0) for g in ("R0", "R1", "R2"))
        or verified["admitted_raw"] < 1
    ):
        raise ValueError("minute_grade_population_or_admission_mismatch")
    manifest = json.loads(manifest_path.read_text())
    revised = manifest.get("date_slice_revised_securities", [])
    if not isinstance(revised, list):
        raise ValueError("minute_grade_revised_cohort_invalid")
    return {
        "grades": {g: grades.get(g, 0) for g in GRADE_KEYS},
        "research_ready": verified["research_ready"],
        "manifest_sha256": manifest_sha,
        "revised_count": len(revised),
        "package": str(package),
    }


def assess_minute_delivery(
    *,
    target: date,
    phase: str,
    at: datetime,
    calendar: list[dict[str, str]],
    minute_root: Path,
    phase_root: Path,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError("unsupported CN01 minute delivery phase")
    calendar_matches = [r for r in calendar if r.get("trade_date") == target.isoformat()]
    if len(calendar_matches) != 1 or str(calendar_matches[0].get("is_open")) not in {"0", "1"}:
        raise ValueError("missing_or_ambiguous_authoritative_market_calendar")
    if str(calendar_matches[0]["is_open"]) == "0":
        return {"status": "calendar_closed_skip", "mail_event": None}
    checkpoint = alert_checkpoint(target, phase, calendar)
    base = {
        "node": "CN01",
        "phase": phase,
        "market_date": target.isoformat(),
        "checkpoint": checkpoint.isoformat(),
        "kind": "p10_verified_minute_delivery_outcome_v1",
    }
    if at < checkpoint:
        return {**base, "status": "not_due", "mail_event": None}
    state_path = phase_root / f"{target.isoformat()}-{PHASES[phase]}.json"
    state: dict[str, Any] = {}
    reason: str | None = None
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
            if not isinstance(state, dict) or not isinstance(state.get("operations"), list):
                raise ValueError("minute phase ledger schema invalid")
            if (
                state.get("market_date") != target.isoformat()
                or state.get("phase") != PHASES[phase]
            ):
                reason = "minute_phase_ledger_identity_mismatch"
            elif state.get("inflight_operation") is not None:
                if _bounded_inflight(state, at):
                    return {**base, "status": "defer", "mail_event": None}
                reason = "minute_phase_operation_stale_or_dead"
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            reason = f"minute_phase_ledger_invalid:{type(exc).__name__}"
    else:
        reason = "minute_phase_ledger_missing"
    terminal = state.get("terminal_disposition") if not reason else None
    if not reason and terminal not in ACCEPTED_TERMINALS[phase]:
        reason = f"minute_phase_not_completed:{terminal or 'missing'}"
    if not reason:
        required_operation = (
            "warehouse-build" if phase == "minute_T" else
            "warehouse-immutable-correction" if terminal == "grade_qualified" else
            "native-source-maturity-canary"
        )
        if not any(
            op.get("operation_id") == required_operation and op.get("outcome") == "success"
            for op in state.get("operations", [])
        ):
            reason = "minute_required_operation_success_receipt_missing"
    grade: dict[str, Any] | None = None
    try:
        grade = verified_minute_grade(minute_root, target)
    except Exception as exc:
        # Any decoder/Parquet/physical-verifier error must fail delivery,
        # never suppress the only final email at the checkpoint.
        if not reason:
            reason = f"minute_verified_product_unavailable:{type(exc).__name__}:{str(exc)[:120]}"
    if grade is not None and not reason and grade["grades"]["R3"]:
        reason = f"R3_unqualified_securities:{grade['grades']['R3']}"
    passed = reason is None and grade is not None
    return {
        **base,
        "status": "pass" if passed else "fail",
        "mail_event": (
            "p10_cn01_minute_delivery_pass" if passed
            else "p10_cn01_minute_delivery_fail"
        ),
        "reason": reason,
        "terminal_disposition": terminal,
        "grade": grade,
    }
