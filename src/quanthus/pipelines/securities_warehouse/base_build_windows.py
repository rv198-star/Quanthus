"""Contract-scoped window closure, preserving the exact inherited held set.

Legacy acquisition attempts are evidence, not final windows. New Daily windows
use their own security-day contract; no native supplier partition is invented.
This stage verifies plans, terminal membership and pinned receipt closure, not
the entire historical native price stream, which remains an independent audit.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget
from .base24_native5m_acquire import CONTRACT as LEGACY_CONTRACT
from .base24_native5m_acquire import _plan, _verify_held
from .base_build_inputs import GENERIC_KINDS, _tree, canonical, file_sha, identity, member
from .base_build_period import LEDGER_CONTRACT

CONTRACT = "p10-standard-base-window-closure-v1"
COLUMNS = (
    "window_id",
    "contract",
    "scope_identity",
    "status",
    "disposition",
    "evidence_identity",
    "venue",
    "stock_code",
    "first_date",
    "last_date",
)
PLAN_PATH = "payload/base24-native5m/denominator/missing-window-plan.json"
ACQUISITION = "payload/base24-native5m/acquisition"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("BUILD_WINDOWS_OBJECT_REQUIRED")
    return value


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def schema() -> Any:
    pa = importlib.import_module("pyarrow")
    return pa.schema([pa.field(name, pa.string(), nullable=False) for name in COLUMNS])


def held_coordinates(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return sorted(
        (
            {k: r[k] for k in ("contract", "window_id", "scope_identity")}
            for r in rows
            if r["status"] == "held"
        ),
        key=lambda r: (r["contract"], r["window_id"]),
    )


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    seen = set()
    intervals: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    counts: Counter[str] = Counter()
    for row in rows:
        if (
            set(row) != set(COLUMNS)
            or any(not isinstance(v, str) or not v for v in row.values())
            or row["status"] not in {"completed", "held", "failed", "pending"}
            or date.fromisoformat(row["first_date"]) > date.fromisoformat(row["last_date"])
        ):
            raise BoundedError("BUILD_WINDOWS_ROW_INVALID")
        key = row["contract"], row["window_id"]
        if key in seen:
            raise BoundedError("BUILD_WINDOWS_DUPLICATE_ID")
        seen.add(key)
        intervals.setdefault((row["contract"], row["venue"], row["stock_code"]), []).append(
            (row["first_date"], row["last_date"])
        )
        counts[row["status"]] += 1
    for spans in intervals.values():
        spans.sort()
        if any(left[1] >= right[0] for left, right in zip(spans, spans[1:], strict=False)):
            raise BoundedError("BUILD_WINDOWS_DIFFERENT_IDS_OVERLAP")
    return {
        "plan_total": len(rows),
        **{k: counts[k] for k in ("completed", "held", "failed", "pending")},
    }


def _legacy(
    predecessor: Path, manifest: dict[str, Any], budget: Budget
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan_path = member(predecessor, PLAN_PATH)
    raw = plan_path.read_bytes()
    plan_sha = hashlib.sha256(raw).hexdigest()
    _, windows = _plan(raw)
    acquisition = member(predecessor, ACQUISITION)
    checkpoint = _load(member(acquisition, "CHECKPOINT.json"))
    declarations = {r["path"]: r for r in manifest["content"]["files"]}
    if (
        checkpoint.get("plan_sha256") != plan_sha
        or checkpoint.get("contract") != LEGACY_CONTRACT
        or checkpoint.get("total") != len(windows)
        or checkpoint.get("plan_total") != len(windows)
        or member(acquisition, "READY").read_text().strip()
        != file_sha(member(acquisition, "CHECKPOINT.json"))[7:]
        or (acquisition / "staging").exists()
    ):
        raise BoundedError("BUILD_WINDOWS_LEGACY_CHECKPOINT_CHANGED")
    expected = {w["partition_id"] for w in windows}
    actual = {}
    for disposition, directory in (("completed", "partitions"), ("held", "held")):
        path = member(acquisition, directory)
        # Product inventories contain files, so an empty terminal directory is
        # legitimately absent after archive recovery. Exact membership below
        # still rejects a missing nonempty terminal set.
        if not path.exists():
            continue
        for child in path.iterdir():
            child = member(path, child.name)
            if not child.is_dir() or child.name in actual or child.name not in expected:
                raise BoundedError("BUILD_WINDOWS_LEGACY_TERMINAL_SET_CHANGED")
            actual[child.name] = disposition
    if set(actual) != expected:
        raise BoundedError("BUILD_WINDOWS_LEGACY_TERMINAL_SET_CHANGED")
    rows = []
    for window in windows:
        budget.check()
        wid = window["partition_id"]
        status = actual[wid]
        directory = member(acquisition, ("held/" if status == "held" else "partitions/") + wid)
        if status == "held":
            _verify_held(directory, acquisition / "failures", window, plan_sha)
            receipt_path = member(directory, "HOLD.json")
        else:
            receipt_path = member(directory, "RECEIPT.json")
            receipt = _load(receipt_path)
            files = receipt.get("files", {})
            if (
                receipt.get("partition_id") != wid
                or receipt.get("contract") != LEGACY_CONTRACT
                or receipt.get("plan_sha256") != plan_sha
                or "exact.parquet" not in files
                or member(directory, "READY").read_text().strip() != file_sha(receipt_path)[7:]
            ):
                raise BoundedError("BUILD_WINDOWS_LEGACY_COMPLETED_RECEIPT_CHANGED")
            # Full source payload bytes are admitted by the shared frozen-input
            # prerequisite. This small stage verifies the original declaration
            # against that sealed predecessor, without decoding prices again.
            for relative in files:
                path = member(directory, relative)
                declared = declarations.get(path.relative_to(predecessor).as_posix(), {})
                expected_sha = files[relative]
                expected_sha = (
                    expected_sha if expected_sha.startswith("sha256:") else "sha256:" + expected_sha
                )
                if declared.get("sha256") != expected_sha or not path.is_file():
                    raise BoundedError("BUILD_WINDOWS_LEGACY_COMPLETED_MEMBER_NOT_IN_PREDECESSOR")
        rows.append(
            {
                "window_id": wid,
                "contract": LEGACY_CONTRACT,
                "scope_identity": identity(window),
                "status": status,
                "disposition": "INHERITED_ORIGINAL_" + status.upper(),
                "evidence_identity": file_sha(receipt_path),
                "venue": window["venue"],
                "stock_code": window["code"][3:],
                "first_date": window["start_date"],
                "last_date": window["end_date"],
            }
        )
    counts = validate_rows(rows)
    declared = manifest.get("content", {}).get("native5m", {})
    if (
        any(checkpoint.get(k) != counts[k] for k in ("completed", "held", "failed"))
        or declared.get("completed_partitions") != counts["completed"]
        or declared.get("held_partitions") != counts["held"]
        or counts["failed"]
        or counts["pending"]
    ):
        raise BoundedError("BUILD_WINDOWS_LEGACY_FINAL_COUNTS_CHANGED")
    return rows, {
        "kind": "legacy_base24_native_windows",
        "plan_sha256": "sha256:" + plan_sha,
        "checkpoint_sha256": file_sha(member(acquisition, "CHECKPOINT.json")),
        "plan_path": PLAN_PATH,
        "acquisition_path": ACQUISITION,
        "counts": counts,
    }


def read_predecessor(
    predecessor: Path, budget: Budget
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = _load(member(predecessor, "manifest.json"))
    if member(predecessor, "READY").read_text().strip() != file_sha(predecessor / "manifest.json"):
        raise BoundedError("BUILD_WINDOWS_PREDECESSOR_READY_CHANGED")
    if manifest.get("kind") == "quanthus_ods_base24_bundle":
        return _legacy(predecessor, manifest, budget)
    if manifest.get("kind") == GENERIC_KINDS["ods"]:
        receipt = audit_windows(member(predecessor, "evidence/windows"), budget)
        pq = importlib.import_module("pyarrow.parquet")
        rows = (
            pq.ParquetFile(member(predecessor, "evidence/windows/WINDOWS.parquet"))
            .read()
            .to_pylist()
        )
        return rows, {
            "kind": "standard_base_windows",
            "window_identity": receipt["identity"],
            "counts": receipt["counts"],
            "predecessor_manifest": file_sha(predecessor / "manifest.json"),
        }
    raise BoundedError("BUILD_WINDOWS_PREDECESSOR_KIND_UNSUPPORTED")


def period_windows(
    period: Path, binding: dict[str, Any], budget: Budget
) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    admission = _load(member(period, "ADMISSION.json"))
    ledger = member(period, "security-days.parquet")
    if (
        admission.get("binding") != binding
        or admission.get("identity")
        != identity({k: v for k, v in admission.items() if k != "identity"})
        or admission.get("ledger_sha256") != file_sha(ledger)
        or admission.get("new_window_contract") != LEDGER_CONTRACT
    ):
        raise BoundedError("BUILD_WINDOWS_PERIOD_ADMISSION_CHANGED")
    pq = importlib.import_module("pyarrow.parquet")
    incoming = []
    for batch in pq.ParquetFile(ledger).iter_batches(batch_size=16384):
        budget.check()
        for row in batch.to_pylist():
            day = row["market_date"]
            scope = {
                "contract": LEDGER_CONTRACT,
                "venue": row["venue"],
                "stock_code": row["stock_code"],
                "market_date": day,
                "intervals": ["1d", "5m"],
            }
            if (
                row["window_id"] != identity(scope)
                or row["status"] != "completed"
                or row["disposition"] not in {"R0", "SUSPENDED"}
                or not binding["predecessor_through"] < day <= binding["through_date"]
            ):
                raise BoundedError("BUILD_WINDOWS_NEW_WINDOW_UNQUALIFIED_OR_OUT_OF_SCOPE")
            incoming.append(
                {
                    "window_id": row["window_id"],
                    "contract": LEDGER_CONTRACT,
                    "scope_identity": identity(scope),
                    "status": row["status"],
                    "disposition": row["disposition"],
                    "evidence_identity": row["parent_daily_manifest_sha256"],
                    "venue": row["venue"],
                    "stock_code": row["stock_code"],
                    "first_date": day,
                    "last_date": day,
                }
            )
    return admission, ledger, incoming


def build_windows(
    *, predecessor: Path, period: Path, binding: dict[str, Any], output: Path, budget: Budget
) -> dict[str, Any]:
    output = budget.destination(output)
    if output.exists():
        raise BoundedError("BUILD_WINDOWS_OUTPUT_EXISTS_PRESERVED")
    inherited, origin = read_predecessor(predecessor, budget)
    baseline = held_coordinates(inherited)
    admission, ledger, incoming = period_windows(period, binding, budget)
    pq, pa = importlib.import_module("pyarrow.parquet"), importlib.import_module("pyarrow")
    counts = validate_rows(inherited + incoming)
    if (
        len(incoming) != admission["new_windows"]
        or counts["failed"]
        or counts["pending"]
        or held_coordinates(inherited + incoming) != baseline
    ):
        raise BoundedError("BUILD_WINDOWS_FINAL_CLOSURE_OR_HELD_SET_CHANGED")
    output.mkdir()
    rows = sorted(inherited + incoming, key=lambda r: (r["contract"], r["window_id"]))
    path = output / "WINDOWS.parquet"
    pq.write_table(
        pa.Table.from_pylist(rows, schema=schema()),
        path,
        compression="zstd",
        version="2.6",
        row_group_size=16384,
    )
    plan = {
        "contract": CONTRACT,
        "binding": binding,
        "predecessor": origin,
        "inherited_held": baseline,
        "inherited_windows": len(inherited),
        "new_windows": len(incoming),
        "period_admission_identity": admission["identity"],
        "new_window_contract": LEDGER_CONTRACT,
        "new_ledger_sha256": file_sha(ledger),
        "ledger_sha256": file_sha(path),
        "counts": counts,
    }
    plan["identity"] = identity(plan)
    _save(output / "PLAN.json", plan)
    _save(
        output / "CHECKPOINT.json",
        {
            "contract": CONTRACT,
            "plan_sha256": file_sha(output / "PLAN.json"),
            "ledger_sha256": file_sha(path),
            **counts,
            "held_set_identity": identity(baseline),
            "quality_held_added": counts["held"] - len(baseline),
        },
    )
    return audit_windows(output, budget)


def audit_windows(root: Path, budget: Budget) -> dict[str, Any]:
    if _tree(root) != {"PLAN.json", "CHECKPOINT.json", "WINDOWS.parquet"}:
        raise BoundedError("BUILD_WINDOWS_PHYSICAL_FILE_SET_CHANGED")
    plan, checkpoint = _load(member(root, "PLAN.json")), _load(member(root, "CHECKPOINT.json"))
    ledger = member(root, "WINDOWS.parquet")
    if (
        plan.get("contract") != CONTRACT
        or plan.get("identity") != identity({k: v for k, v in plan.items() if k != "identity"})
        or checkpoint.get("plan_sha256") != file_sha(root / "PLAN.json")
        or file_sha(ledger) != plan.get("ledger_sha256")
        or checkpoint.get("ledger_sha256") != plan["ledger_sha256"]
    ):
        raise BoundedError("BUILD_WINDOWS_PLAN_OR_LEDGER_CHANGED")
    rows = []
    pq = importlib.import_module("pyarrow.parquet")
    physical = pq.ParquetFile(ledger)
    if not physical.schema_arrow.equals(schema(), check_metadata=False):
        raise BoundedError("BUILD_WINDOWS_PHYSICAL_SCHEMA_CHANGED")
    for batch in physical.iter_batches(batch_size=16384):
        budget.check()
        rows.extend(batch.to_pylist())
    counts = validate_rows(rows)
    held = held_coordinates(rows)
    if (
        counts != plan["counts"]
        or any(checkpoint.get(k) != v for k, v in counts.items())
        or plan["inherited_windows"] + plan["new_windows"] != counts["plan_total"]
        or held != plan["inherited_held"]
        or identity(held) != checkpoint.get("held_set_identity")
        or counts["failed"]
        or counts["pending"]
        or checkpoint.get("quality_held_added") != 0
    ):
        raise BoundedError("BUILD_WINDOWS_FINAL_CLOSURE_OR_HELD_SET_CHANGED")
    result = {
        "contract": CONTRACT,
        "plan_sha256": file_sha(root / "PLAN.json"),
        "ledger_sha256": file_sha(ledger),
        "counts": counts,
        "held_set_identity": identity(held),
        "new_quality_held": counts["held"] - len(plan["inherited_held"]),
        "historical_price_replay": "SEPARATE_AUDIT_REQUIRED",
        "formal_READY": False,
    }
    result["identity"] = identity(result)
    return result


def verify_window_ancestry(
    *, root: Path, predecessor: Path, period: Path, budget: Budget
) -> dict[str, Any]:
    """Reconstruct exact closure from the embedded predecessor and period.

    Self-consistent ledger/checkpoint hashes cannot redefine inherited held or
    drop otherwise completed windows. The owning ODS audit first verifies the
    original predecessor archive and its seal independently.
    """
    receipt = audit_windows(root, budget)
    plan = _load(member(root, "PLAN.json"))
    inherited, origin = read_predecessor(predecessor, budget)
    admission = _load(member(period, "ADMISSION.json"))
    _, ledger, incoming = period_windows(period, admission["binding"], budget)
    expected = sorted(inherited + incoming, key=lambda r: (r["contract"], r["window_id"]))
    pq = importlib.import_module("pyarrow.parquet")
    actual = pq.ParquetFile(member(root, "WINDOWS.parquet")).read().to_pylist()
    if (
        actual != expected
        or plan["predecessor"] != origin
        or plan["binding"] != admission["binding"]
        or plan["period_admission_identity"] != admission["identity"]
        or plan["new_window_contract"] != LEDGER_CONTRACT
        or plan["new_ledger_sha256"] != file_sha(ledger)
        or plan["inherited_windows"] != len(inherited)
        or plan["new_windows"] != len(incoming)
        or len(incoming) != admission["new_windows"]
        or plan["inherited_held"] != held_coordinates(inherited)
    ):
        raise BoundedError("BUILD_WINDOWS_PREDECESSOR_OR_PERIOD_ANCESTRY_CHANGED")
    return receipt
