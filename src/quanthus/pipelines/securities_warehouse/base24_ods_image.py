"""Independent ODS Base 2.4 image from the retained Base 2.3 raw closure.

The image keeps the exact non-1m members selected from ODS 2.3 and adds the
bounded supplier-native 5m acquisition, including wire bytes and failure/hold
evidence.  Source paths are archival strings after materialization and are never
dereferenced by the offline auditor.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from .base22 import canonical, digest, file_sha
from .base22_sources import _fingerprint, _member, _tree, disjoint_output, physical_path
from .base24_native5m_acquire import _plan, _verify_held, _verify_sealed
from .base24_native5m_bounded import (
    Base24BoundedLimitationError,
    validate_bounded_ledger_package,
)

KIND = "quanthus_ods_base24_bundle"
CONTRACT = "p10-base24-ods-1d-native5m-v1"
PURPOSE = "BASE24_SOURCE_GROUNDED_1D_NATIVE5M_RECONSTRUCTION"
THROUGH = "2026-09-30"
SELECTION_CONTRACT = "p10-base24-ods23-default-member-selection-v1"
SCOPE_CONTRACT = "p10-base24-default-1d-native5m-v1"
LEDGER_CONTRACT = "p10-base24-native5m-coverage-ledger-package-v1"
ACQUISITION_CONTRACT = "p10-base24-native5m-acquisition-v1"
SUPPLEMENTAL_CLOSURE_CONTRACT = (
    "p10-base24-native5m-supplemental-source-closure-v1"
)
VERIFY_WORKERS = min(8, max(1, os.cpu_count() or 1))
VERIFY_BATCH = 256
_T = TypeVar("_T")
_R = TypeVar("_R")


class Base24OdsError(RuntimeError):
    """Stable reason codes; source row values must never be included."""


def _parallel_ordered(
    values: Sequence[_T], function: Callable[[_T], _R], *, workers: int = VERIFY_WORKERS
) -> Iterator[_R]:
    """Evaluate bounded batches concurrently while yielding deterministic order."""
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="base24-ods") as pool:
        for start in range(0, len(values), VERIFY_BATCH):
            yield from pool.map(function, values[start : start + VERIFY_BATCH])


def _sha(value: str) -> str:
    return value if value.startswith("sha256:") else "sha256:" + value


def _load_object(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as exc:
        raise Base24OdsError(reason) from exc
    if not isinstance(value, dict):
        raise Base24OdsError(reason)
    return value


def _source_member(source: Path, relative: str, role: str, target: str) -> dict[str, Any]:
    path = _member(source, relative)
    return {
        "path": target,
        "source_path": str(path),
        "source_relative": relative,
        "source_root_archival": str(source),
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": file_sha(path),
        "source_fingerprint": list(_fingerprint(path)),
    }


def _validate_selection(
    predecessor: Path, selection_path: Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = _member(predecessor, "manifest.json")
    manifest = _load_object(manifest_path, "BASE24_ODS_PREDECESSOR_MANIFEST_INVALID")
    if (
        manifest.get("kind") != "quanthus_ods_base_bundle"
        or manifest.get("schema_version") != 1
        or manifest.get("content", {}).get("through_date") != THROUGH
        or manifest.get("content_digest") != digest(manifest["content"])
        or _member(predecessor, "READY").read_text(encoding="ascii").strip()
        != file_sha(manifest_path)
    ):
        raise Base24OdsError("BASE24_ODS_PREDECESSOR_SEAL_CHANGED")
    wrapper = _load_object(selection_path, "BASE24_ODS_SELECTION_INVALID")
    selection = wrapper.get("selection")
    if (
        wrapper.get("contract") != SELECTION_CONTRACT
        or not isinstance(selection, dict)
        or selection.get("contract_id") != SCOPE_CONTRACT
        or selection.get("monthly_manifest_sha256") != file_sha(manifest_path).removeprefix(
            "sha256:"
        )
        or selection.get("optional_1m") != "NOT_INCLUDED"
        or selection.get("retained_sources_modified") is not False
        or wrapper.get("source_modified") is not False
    ):
        raise Base24OdsError("BASE24_ODS_SELECTION_IDENTITY_CHANGED")
    included = selection.get("included")
    excluded = selection.get("excluded")
    if not isinstance(included, list) or not isinstance(excluded, list) or not excluded:
        raise Base24OdsError("BASE24_ODS_SELECTION_MEMBER_SET_INVALID")
    all_records = manifest["content"].get("files")
    if not isinstance(all_records, list):
        raise Base24OdsError("BASE24_ODS_PREDECESSOR_MANIFEST_INVALID")
    declared = {row["path"]: row for row in all_records}
    chosen: dict[str, dict[str, Any]] = {}
    rejected: set[str] = set()
    for disposition, rows in (("included", included), ("excluded", excluded)):
        for row in rows:
            path = row.get("path")
            original = declared.get(path)
            if (
                not isinstance(path, str)
                or original is None
                or path in chosen
                or path in rejected
                or _sha(str(row.get("sha256"))) != _sha(str(original.get("sha256")))
                or row.get("size_bytes") != original.get("size_bytes")
                or row.get("role") != original.get("role")
            ):
                raise Base24OdsError("BASE24_ODS_SELECTION_MEMBER_SET_INVALID")
            if disposition == "included":
                chosen[path] = row
            else:
                if row.get("disposition") != "NOT_INCLUDED" or not str(
                    row.get("reason", "")
                ).startswith("OPTIONAL_1M_"):
                    raise Base24OdsError("BASE24_ODS_EXCLUDED_MEMBER_REASON_INVALID")
                rejected.add(path)
    if set(declared) != set(chosen) | rejected:
        raise Base24OdsError("BASE24_ODS_SELECTION_MEMBER_SET_INVALID")
    if any(
        Path(path).name
        in {"intraday_1m_bars.parquet", "intraday_1m_coverage.parquet", "intraday_1m_meta.json"}
        for path in chosen
    ):
        raise Base24OdsError("BASE24_ODS_OPTIONAL_1M_SELECTED")
    return wrapper, chosen


def _validate_acquisition(acquisition: Path, plan_path: Path) -> dict[str, Any]:
    plan_bytes = plan_path.read_bytes()
    plan_sha = file_sha(plan_path).removeprefix("sha256:")
    _, windows = _plan(plan_bytes)
    checkpoint_path = _member(acquisition, "CHECKPOINT.json")
    checkpoint = _load_object(checkpoint_path, "BASE24_ODS_ACQUISITION_CHECKPOINT_INVALID")
    if (
        checkpoint.get("contract") != ACQUISITION_CONTRACT
        or checkpoint.get("status") not in {"COMPLETE", "COMPLETE_WITH_HELD"}
        or checkpoint.get("plan_sha256") != plan_sha
        or checkpoint.get("total") != len(windows)
        or _member(acquisition, "READY").read_text(encoding="ascii").strip()
        != file_sha(checkpoint_path).removeprefix("sha256:")
    ):
        raise Base24OdsError("BASE24_ODS_ACQUISITION_CHECKPOINT_INVALID")
    def verify(window: dict[str, Any]) -> str:
        partition = acquisition / "partitions" / window["partition_id"]
        hold = acquisition / "held" / window["partition_id"]
        if partition.is_dir() and not hold.exists():
            _verify_sealed(partition, window, plan_sha)
            return "completed"
        elif hold.is_dir() and not partition.exists():
            _verify_held(hold, acquisition / "failures", window, plan_sha)
            return "held"
        else:
            raise Base24OdsError("BASE24_ODS_ACQUISITION_PARTITION_CLOSURE_INVALID")
    dispositions = list(_parallel_ordered(windows, verify))
    completed = dispositions.count("completed")
    held = dispositions.count("held")
    if completed != checkpoint.get("completed") or held != checkpoint.get("held"):
        raise Base24OdsError("BASE24_ODS_ACQUISITION_PARTITION_CLOSURE_INVALID")
    if (acquisition / "staging").exists():
        raise Base24OdsError("BASE24_ODS_ACQUISITION_STAGING_REMAINS")
    return checkpoint


def _validate_ledger(ledger: Path, acquisition: Path) -> dict[str, Any]:
    manifest_path = _member(ledger, "manifest.json")
    manifest = _load_object(manifest_path, "BASE24_ODS_LEDGER_INVALID")
    if (
        manifest.get("contract") != LEDGER_CONTRACT
        or _member(ledger, "READY").read_text(encoding="ascii").strip()
        != file_sha(manifest_path).removeprefix("sha256:")
        or manifest.get("inputs", {}).get("acquisition_READY_SHA256")
        != file_sha(acquisition / "READY").removeprefix("sha256:")
    ):
        raise Base24OdsError("BASE24_ODS_LEDGER_INVALID")
    expected = {"manifest.json", "READY", *(row["path"] for row in manifest.get("files", []))}
    if _tree(ledger) != expected:
        raise Base24OdsError("BASE24_ODS_LEDGER_FILE_SET_INVALID")
    for row in manifest["files"]:
        member = _member(ledger, row["path"])
        if member.stat().st_size != row["size_bytes"] or file_sha(member) != _sha(row["sha256"]):
            raise Base24OdsError("BASE24_ODS_LEDGER_MEMBER_INVALID")
    result = _load_object(_member(ledger, "result.safe.json"), "BASE24_ODS_LEDGER_INVALID")
    try:
        validate_bounded_ledger_package(root=ledger, manifest=manifest, result=result)
    except Base24BoundedLimitationError as exc:
        raise Base24OdsError("BASE24_ODS_LEDGER_INVALID") from exc
    if manifest.get("status") not in {"PASS", "PASS_WITH_BOUNDED_5M_LIMITATIONS"}:
        raise Base24OdsError("BASE24_ODS_LEDGER_INVALID")
    return manifest


def _validate_contract(path: Path) -> dict[str, Any]:
    contract = _load_object(path, "BASE24_ODS_CONTRACT_INVALID")
    if (
        contract.get("contract_id") != CONTRACT
        or contract.get("schema_version") != 1
        or contract.get("kind") != KIND
        or contract.get("purpose") != PURPOSE
        or contract.get("business_through") != THROUGH
        or contract.get("business_timezone") != "Asia/Shanghai"
        or contract.get("default_frequencies") != ["1d", "5m"]
        or contract.get("optional_1m") != "NOT_INCLUDED"
        or contract.get("predecessor_is_final_base24_ods") is not False
        or contract.get("native5m_acquisition_contract") != ACQUISITION_CONTRACT
        or contract.get("coverage_ledger_contract") != LEDGER_CONTRACT
        or contract.get("consumer_admission") != "NOT_GRANTED"
        or contract.get("production_execution_authorized") is not False
        or contract.get("physical_rules", {}).get("READY")
        != "LAST_WRITE_AFTER_OFFLINE_AUDIT"
    ):
        raise Base24OdsError("BASE24_ODS_CONTRACT_INVALID")
    return contract


def _validate_supplemental_closure(
    path: Path,
    *,
    acquisition: Path,
    ledger_manifest_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    closure = _load_object(path, "BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
    frozen = {key: value for key, value in closure.items() if key != "identity"}
    members = closure.get("members")
    required = closure.get("required_exact_source_paths")
    if (
        closure.get("contract") != SUPPLEMENTAL_CLOSURE_CONTRACT
        or closure.get("identity") != digest(frozen)
        or closure.get("primary_acquisition_root_archival") != str(acquisition)
        or _sha(str(closure.get("ledger_manifest_sha256")))
        != _sha(ledger_manifest_sha256)
        or not isinstance(members, list)
        or not members
        or not isinstance(required, list)
        or not required
        or len(required) != len(set(required))
    ):
        raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
    validated: list[dict[str, Any]] = []
    observed_sources: set[str] = set()
    observed_targets: set[str] = set()
    for row in members:
        if not isinstance(row, dict):
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
        root_value = row.get("source_root_archival")
        relative = row.get("source_relative")
        source_value = row.get("source_path")
        if (
            not isinstance(root_value, str)
            or not isinstance(relative, str)
            or not isinstance(source_value, str)
        ):
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
        root = physical_path(Path(root_value))
        if root == acquisition or root in acquisition.parents or acquisition in root.parents:
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
        source = _member(root, relative)
        if source != physical_path(Path(source_value)) or str(source) in observed_sources:
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
        root_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
        target = f"payload/base24-native5m/supplemental/{root_id}/{relative}"
        if target in observed_targets:
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
        if (
            row.get("size_bytes") != source.stat().st_size
            or _sha(str(row.get("sha256"))) != file_sha(source)
        ):
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
        observed_sources.add(str(source))
        observed_targets.add(target)
        validated.append(
            {
                "path": target,
                "source_path": str(source),
                "source_relative": relative,
                "source_root_archival": str(root),
                "role": "native5m_supplemental_source",
                "size_bytes": source.stat().st_size,
                "sha256": file_sha(source),
                "source_fingerprint": list(_fingerprint(source)),
            }
        )
    if not set(required).issubset(observed_sources):
        raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
    return closure, validated


def freeze_ods24_plan(
    *,
    predecessor_root: Path,
    selection_path: Path,
    acquisition_root: Path,
    window_plan_path: Path,
    ledger_root: Path,
    contract_path: Path,
    supplemental_closure_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and freeze the complete real source closure before any write."""
    predecessor = physical_path(predecessor_root)
    acquisition = physical_path(acquisition_root)
    ledger = physical_path(ledger_root)
    selection_path = physical_path(selection_path)
    window_plan_path = physical_path(window_plan_path)
    contract_path = physical_path(contract_path)
    _validate_contract(contract_path)
    selection, included = _validate_selection(predecessor, selection_path)
    checkpoint = _validate_acquisition(acquisition, window_plan_path)
    ledger_manifest = _validate_ledger(ledger, acquisition)
    supplemental: dict[str, Any] | None = None
    supplemental_members: list[dict[str, Any]] = []
    if supplemental_closure_path is not None:
        supplemental_closure_path = physical_path(supplemental_closure_path)
        supplemental, supplemental_members = _validate_supplemental_closure(
            supplemental_closure_path,
            acquisition=acquisition,
            ledger_manifest_sha256=file_sha(ledger / "manifest.json"),
        )
    files: list[dict[str, Any]] = []
    targets: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        if item["path"] in targets:
            raise Base24OdsError("BASE24_ODS_TARGET_COLLISION")
        targets.add(item["path"])
        files.append(item)

    predecessor_rows = sorted(included.items())

    def predecessor_member(value: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        relative, row = value
        item = _source_member(predecessor, relative, "retained_ods23_member", relative)
        if item["size_bytes"] != row["size_bytes"] or item["sha256"] != _sha(row["sha256"]):
            raise Base24OdsError("BASE24_ODS_PREDECESSOR_MEMBER_CHANGED")
        return item

    for item in _parallel_ordered(predecessor_rows, predecessor_member):
        add(item)

    acquisition_paths = sorted(_tree(acquisition))
    for item in _parallel_ordered(
        acquisition_paths,
        lambda relative: _source_member(
            acquisition,
            relative,
            "native5m_raw_acquisition",
            "payload/base24-native5m/acquisition/" + relative,
        ),
    ):
        add(item)
    ledger_paths = sorted(_tree(ledger))
    for item in _parallel_ordered(
        ledger_paths,
        lambda relative: _source_member(
            ledger,
            relative,
            "native5m_coverage_evidence",
            "payload/base24-native5m/coverage/" + relative,
        ),
    ):
        add(item)
    for item in supplemental_members:
        add(item)
    if supplemental_closure_path is not None:
        add(
            _source_member(
                supplemental_closure_path.parent,
                supplemental_closure_path.name,
                "native5m_supplemental_closure",
                "payload/source-plan/native5m-supplemental-closure.json",
            )
        )
    add(
        _source_member(
            window_plan_path.parent,
            window_plan_path.name,
            "native5m_denominator",
            "payload/base24-native5m/denominator/missing-window-plan.json",
        )
    )
    add(
        _source_member(
            selection_path.parent,
            selection_path.name,
            "base24_source_selection",
            "payload/source-plan/ods23-default-selection.json",
        )
    )
    add(
        _source_member(
            contract_path.parent,
            contract_path.name,
            "base24_ods_contract",
            "payload/source-plan/base24-ods-contract.json",
        )
    )
    plan = {
        "contract": CONTRACT,
        "purpose": PURPOSE,
        "business_through": THROUGH,
        "business_timezone": "Asia/Shanghai",
        "default_frequencies": ["1d", "5m"],
        "optional_1m": "NOT_INCLUDED",
        "contract_sha256": file_sha(contract_path),
        "predecessor": {
            "root_archival": str(predecessor),
            "manifest_sha256": file_sha(predecessor / "manifest.json"),
            "selection_identity": selection["identity"],
            "selected_files": len(included),
        },
        "native5m": {
            "acquisition_root_archival": str(acquisition),
            "acquisition_READY_sha256": file_sha(acquisition / "READY"),
            "checkpoint_sha256": file_sha(acquisition / "CHECKPOINT.json"),
            "completed_partitions": checkpoint["completed"],
            "held_partitions": checkpoint["held"],
            "window_plan_sha256": file_sha(window_plan_path),
            "ledger_root_archival": str(ledger),
            "ledger_manifest_sha256": file_sha(ledger / "manifest.json"),
            "ledger_status": ledger_manifest["status"],
            "supplemental_closure_identity": (
                supplemental["identity"] if supplemental is not None else None
            ),
            "supplemental_members": len(supplemental_members),
            "supplemental_required_exact_sources": (
                len(supplemental["required_exact_source_paths"])
                if supplemental is not None
                else 0
            ),
        },
        "files": files,
        "source_mutation": False,
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
    }
    plan["identity"] = digest(plan)
    return plan


def _check_budget(budget: Any, reserve: int = 0) -> None:
    try:
        budget.check(reserve=reserve)
    except TypeError:
        budget.check()


def _receipt_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    roles: dict[str, int] = {}
    exact_files = 0
    for item in plan["files"]:
        role = item["role"]
        roles[role] = roles.get(role, 0) + 1
        if re.fullmatch(
            r"payload/base24-native5m/acquisition/partitions/[0-9a-f]{64}/exact\.parquet",
            item["path"],
        ):
            exact_files += 1
    receipt = {
        "contract": CONTRACT,
        "status": "PASS",
        "plan_identity": plan["identity"],
        "files": len(plan["files"]),
        "logical_bytes": sum(row["size_bytes"] for row in plan["files"]),
        "roles": dict(sorted(roles.items())),
        "completed_native5m_partitions": exact_files,
        "held_native5m_partitions": plan["native5m"]["held_partitions"],
        "supplemental_native5m_members": roles.get("native5m_supplemental_source", 0),
        "source_roots_dereferenced": False,
        "consumer_admission": "NOT_GRANTED",
    }
    receipt["identity"] = digest(receipt)
    return receipt


def materialize_ods24(plan: dict[str, Any], output: Path, budget: Any) -> dict[str, Any]:
    """Make independent files; no hard links, symlinks, or source-root dependency."""
    sources = sorted({Path(item["source_root_archival"]) for item in plan["files"]})
    output = disjoint_output(output, sources)
    staging = disjoint_output(output.parent / ("." + output.name + ".job"), sources)
    if output.exists() or staging.exists():
        raise FileExistsError("BASE24_ODS_OUTPUT_ALREADY_EXISTS")
    frozen = {k: v for k, v in plan.items() if k != "identity"}
    if digest(frozen) != plan.get("identity"):
        raise Base24OdsError("BASE24_ODS_PLAN_CHANGED")
    required = sum(row["size_bytes"] for row in plan["files"]) + len(canonical(plan)) + 4096
    _check_budget(budget, reserve=required)
    def check_source(item: dict[str, Any]) -> None:
        source = physical_path(Path(item["source_path"]))
        if list(_fingerprint(source)) != item["source_fingerprint"]:
            raise Base24OdsError("BASE24_ODS_SOURCE_CHANGED_BEFORE_WRITE")
    list(_parallel_ordered(plan["files"], check_source))
    staging.mkdir()
    try:
        budget.begin_stage("base24-ods-independent-materialization")

        def copy_member(item: dict[str, Any]) -> None:
            source = physical_path(Path(item["source_path"]))
            target = _member(staging, item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            copied = hashlib.sha256()
            with source.open("rb") as src, target.open("xb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    copied.update(chunk)
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            if (
                target.stat().st_nlink != 1
                or target.stat().st_size != item["size_bytes"]
                or "sha256:" + copied.hexdigest() != item["sha256"]
                or list(_fingerprint(source)) != item["source_fingerprint"]
            ):
                raise Base24OdsError("BASE24_ODS_COPIED_BYTES_CHANGED")

        with ThreadPoolExecutor(
            max_workers=VERIFY_WORKERS, thread_name_prefix="base24-ods-copy"
        ) as pool:
            for start in range(0, len(plan["files"]), VERIFY_BATCH):
                _check_budget(budget)
                list(pool.map(copy_member, plan["files"][start : start + VERIFY_BATCH]))
        materialization = {
            **copy.deepcopy(plan),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        (staging / "MATERIALIZATION.json").write_bytes(canonical(materialization) + b"\n")
        staging.rename(output)
    except BaseException:
        # Retain failed staging for diagnosis; never mark it READY.
        raise
    # The copy pass validates every source fingerprint and hashes the bytes
    # written to each independent file.  seal_ods24 performs the one complete
    # offline readback before it writes the final READY marker.
    return _receipt_from_plan(plan)


def _plan_path(root: Path) -> Path:
    sealed = (root / "manifest.json").exists() or (root / "READY").exists()
    return root / ("evidence/MATERIALIZATION.json" if sealed else "MATERIALIZATION.json")


def audit_ods24(
    root: Path, budget: Any, *, expected_plan_identity: str | None = None
) -> dict[str, Any]:
    """Cold, source-denied verification of namespace, hashes and native5m closure."""
    root = physical_path(root)
    plan_path = _plan_path(root)
    plan = _load_object(plan_path, "BASE24_ODS_MATERIALIZATION_INVALID")
    frozen = {k: v for k, v in plan.items() if k not in {"identity", "built_at"}}
    if (
        plan.get("contract") != CONTRACT
        or plan.get("purpose") != PURPOSE
        or plan.get("business_through") != THROUGH
        or plan.get("default_frequencies") != ["1d", "5m"]
        or plan.get("optional_1m") != "NOT_INCLUDED"
        or digest(frozen) != plan.get("identity")
        or expected_plan_identity is not None
        and plan.get("identity") != expected_plan_identity
    ):
        raise Base24OdsError("BASE24_ODS_MATERIALIZATION_INVALID")
    files = plan.get("files")
    if not isinstance(files, list) or len(files) != len({row.get("path") for row in files}):
        raise Base24OdsError("BASE24_ODS_MEMBER_SET_INVALID")
    sealed = plan_path.parent.name == "evidence"
    extras = {"evidence/MATERIALIZATION.json", "manifest.json", "READY"} if sealed else {
        "MATERIALIZATION.json"
    }
    if _tree(root) != {row["path"] for row in files} | extras:
        raise Base24OdsError("BASE24_ODS_PHYSICAL_FILE_SET_CHANGED")
    budget.begin_stage("base24-ods-offline-audit")
    roles: dict[str, int] = {}
    exact_files = 0

    def verify_member(item: dict[str, Any]) -> tuple[str, bool]:
        path = _member(root, item["path"])
        if (
            path.stat().st_nlink != 1
            or path.stat().st_size != item["size_bytes"]
            or file_sha(path) != item["sha256"]
        ):
            raise Base24OdsError("BASE24_ODS_PHYSICAL_BYTES_CHANGED")
        exact = bool(re.fullmatch(
            r"payload/base24-native5m/acquisition/partitions/[0-9a-f]{64}/exact\.parquet",
            item["path"],
        ))
        return item["role"], exact

    with ThreadPoolExecutor(
        max_workers=VERIFY_WORKERS, thread_name_prefix="base24-ods-audit"
    ) as pool:
        for start in range(0, len(files), VERIFY_BATCH):
            _check_budget(budget)
            for role, exact in pool.map(verify_member, files[start : start + VERIFY_BATCH]):
                roles[role] = roles.get(role, 0) + 1
                exact_files += int(exact)
    if exact_files != plan["native5m"]["completed_partitions"]:
        raise Base24OdsError("BASE24_ODS_NATIVE5M_PARTITION_CLOSURE_CHANGED")
    supplemental_members = roles.get("native5m_supplemental_source", 0)
    if supplemental_members != plan["native5m"].get("supplemental_members", 0):
        raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
    supplemental_identity = plan["native5m"].get("supplemental_closure_identity")
    if supplemental_identity is not None:
        embedded_closure = _load_object(
            _member(root, "payload/source-plan/native5m-supplemental-closure.json"),
            "BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID",
        )
        embedded_frozen = {
            key: value for key, value in embedded_closure.items() if key != "identity"
        }
        if (
            embedded_closure.get("contract") != SUPPLEMENTAL_CLOSURE_CONTRACT
            or embedded_closure.get("identity") != supplemental_identity
            or digest(embedded_frozen) != supplemental_identity
            or len(embedded_closure.get("required_exact_source_paths", []))
            != plan["native5m"].get("supplemental_required_exact_sources")
        ):
            raise Base24OdsError("BASE24_ODS_SUPPLEMENTAL_SOURCE_CLOSURE_INVALID")
    selection = _load_object(
        _member(root, "payload/source-plan/ods23-default-selection.json"),
        "BASE24_ODS_SELECTION_INVALID",
    )
    selected = selection.get("selection", {})
    included_paths = {row.get("path") for row in selected.get("included", [])}
    retained_paths = {
        row["path"] for row in files if row.get("role") == "retained_ods23_member"
    }
    if (
        selection.get("identity") != plan["predecessor"]["selection_identity"]
        or len(selected.get("included", [])) != plan["predecessor"]["selected_files"]
        or selected.get("optional_1m") != "NOT_INCLUDED"
        or retained_paths != included_paths
        or retained_paths & {row.get("path") for row in selected.get("excluded", [])}
    ):
        raise Base24OdsError("BASE24_ODS_SELECTION_IDENTITY_CHANGED")
    contract_path = _member(root, "payload/source-plan/base24-ods-contract.json")
    _validate_contract(contract_path)
    if file_sha(contract_path) != plan.get("contract_sha256"):
        raise Base24OdsError("BASE24_ODS_CONTRACT_INVALID")
    acquisition_prefix = "payload/base24-native5m/acquisition/"
    checkpoint = _load_object(
        _member(root, acquisition_prefix + "CHECKPOINT.json"),
        "BASE24_ODS_ACQUISITION_CHECKPOINT_INVALID",
    )
    if (
        checkpoint.get("completed") != plan["native5m"]["completed_partitions"]
        or checkpoint.get("held") != plan["native5m"]["held_partitions"]
        or file_sha(_member(root, acquisition_prefix + "CHECKPOINT.json"))
        != plan["native5m"]["checkpoint_sha256"]
    ):
        raise Base24OdsError("BASE24_ODS_ACQUISITION_CHECKPOINT_INVALID")
    coverage_prefix = "payload/base24-native5m/coverage/"
    ledger_manifest_path = _member(root, coverage_prefix + "manifest.json")
    ledger_manifest = _load_object(ledger_manifest_path, "BASE24_ODS_LEDGER_INVALID")
    ledger_result = _load_object(
        _member(root, coverage_prefix + "result.safe.json"), "BASE24_ODS_LEDGER_INVALID"
    )
    if (
        file_sha(ledger_manifest_path) != plan["native5m"]["ledger_manifest_sha256"]
        or ledger_manifest.get("contract") != LEDGER_CONTRACT
    ):
        raise Base24OdsError("BASE24_ODS_LEDGER_INVALID")
    try:
        validate_bounded_ledger_package(
            root=root / coverage_prefix, manifest=ledger_manifest, result=ledger_result
        )
    except Base24BoundedLimitationError as exc:
        raise Base24OdsError("BASE24_ODS_LEDGER_INVALID") from exc
    receipt = _receipt_from_plan(plan)
    if receipt["roles"] != dict(sorted(roles.items())):
        raise Base24OdsError("BASE24_ODS_MEMBER_ROLE_SET_CHANGED")
    if sealed:
        manifest = _load_object(root / "manifest.json", "BASE24_ODS_SEAL_INVALID")
        declared = {
            row["path"]: (row["size_bytes"], row["sha256"], row["role"])
            for row in manifest.get("content", {}).get("files", [])
        }
        expected_declared = {
            row["path"]: (row["size_bytes"], row["sha256"], row["role"])
            for row in files
        }
        expected_declared["evidence/MATERIALIZATION.json"] = (
            plan_path.stat().st_size,
            file_sha(plan_path),
            "local_governance_evidence",
        )
        if (
            manifest.get("kind") != KIND
            or manifest.get("schema_version") != 1
            or manifest.get("content_digest") != digest(manifest["content"])
            or manifest.get("content", {}).get("audit_identity") != receipt["identity"]
            or len(declared) != len(manifest["content"]["files"])
            or declared != expected_declared
            or (root / "READY").read_text(encoding="ascii").strip()
            != file_sha(root / "manifest.json")
        ):
            raise Base24OdsError("BASE24_ODS_SEAL_INVALID")
    return receipt


def seal_ods24(
    root: Path, receipt: dict[str, Any], *, code_revision: str, budget: Any
) -> dict[str, Any]:
    """Seal an audited candidate. READY is the final write."""
    root = physical_path(root)
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise FileExistsError("BASE24_ODS_ALREADY_SEALED")
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise Base24OdsError("BASE24_ODS_FULL_CODE_REVISION_REQUIRED")
    current = audit_ods24(root, budget, expected_plan_identity=receipt.get("plan_identity"))
    if current != receipt:
        raise Base24OdsError("BASE24_ODS_AUDIT_RECEIPT_CHANGED")
    source = root / "MATERIALIZATION.json"
    target = root / "evidence/MATERIALIZATION.json"
    target.parent.mkdir()
    target.write_bytes(source.read_bytes())
    if file_sha(target) != file_sha(source):
        raise Base24OdsError("BASE24_ODS_LOCAL_PLAN_COPY_CHANGED")
    source.unlink()
    plan = _load_object(target, "BASE24_ODS_MATERIALIZATION_INVALID")
    content_files = [
        {k: row[k] for k in ("path", "size_bytes", "sha256", "role")}
        for row in plan["files"]
    ]
    content_files.append(
        {
            "path": "evidence/MATERIALIZATION.json",
            "size_bytes": target.stat().st_size,
            "sha256": file_sha(target),
            "role": "local_governance_evidence",
        }
    )
    content = {
        "contract": CONTRACT,
        "purpose": PURPOSE,
        "through_date": THROUGH,
        "business_timezone": "Asia/Shanghai",
        "default_frequencies": ["1d", "5m"],
        "optional_1m": "NOT_INCLUDED",
        "contract_sha256": plan["contract_sha256"],
        "plan_identity": plan["identity"],
        "audit_identity": receipt["identity"],
        "files": content_files,
        "predecessor": plan["predecessor"],
        "native5m": plan["native5m"],
        "consumer_admission": "NOT_GRANTED",
    }
    manifest = {
        "schema_version": 1,
        "kind": KIND,
        "bundle_id": root.name,
        "built_at": plan["built_at"],
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": code_revision,
        "content": content,
        "content_digest": digest(content),
    }
    (root / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    # Verify the sealed namespace before the final readiness marker exists.
    expected = {row["path"] for row in plan["files"]} | {
        "evidence/MATERIALIZATION.json",
        "manifest.json",
    }
    if _tree(root) != expected:
        raise Base24OdsError("BASE24_ODS_FINAL_PHYSICAL_SET_CHANGED")
    with (root / "READY").open("x", encoding="ascii") as stream:
        stream.write(file_sha(root / "manifest.json") + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if (
        _tree(root) != expected | {"READY"}
        or (root / "READY").read_text(encoding="ascii").strip()
        != file_sha(root / "manifest.json")
    ):
        raise Base24OdsError("BASE24_ODS_FINAL_PHYSICAL_SET_CHANGED")
    return manifest
