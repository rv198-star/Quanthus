"""Freeze complete authoritative Base/Daily packages without manufacturing READY.

CLI is an input stage of the standard builder, not a completed Base product.
Root mappings are separate from the stable inventory so restored packages can be
verified in another environment without consulting original absolute paths.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .base_build_inputs import (
    GENERIC_KINDS,
    BuildInputError,
    canonical,
    identity,
    inventory,
    inventory_evidence,
)

CONTRACT = "p10-base-build-input-selection-v1"
FROZEN = "p10-base-build-frozen-inputs-v1"
CONTRACT_V2 = "p10-base-build-input-selection-v2"
FROZEN_V2 = "p10-base-build-frozen-inputs-v2"
KINDS = {
    "ods": "quanthus_ods_base24_bundle",
    "dwd": "dwd_base24_native_1d5m_purpose_qualified_candidate_v1",
    "daily": "cn02_base24_daily_1d_native5m_v1",
}
EVIDENCE_PURPOSES = {"calendar_reference", "daily_source_evidence", "historical_quality"}


def _version(value: Any) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
        raise BuildInputError("BUILD_SELECTION_VERSION")
    parts = [int(x) for x in value.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def _frozen_contract(selection: dict[str, Any]) -> str:
    return FROZEN_V2 if selection["contract"] == CONTRACT_V2 else FROZEN


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BuildInputError("BUILD_SELECTION_OBJECT_REQUIRED")
    return value


def validate_selection(value: dict[str, Any]) -> None:
    keys = {"contract", "through_date", "predecessor_through", "expected_days",
            "expected_days_evidence", "packages"}
    v2 = value.get("contract") == CONTRACT_V2
    if v2:
        keys |= {"predecessor_version", "target_version", "evidence"}
    if set(value) != keys or value.get("contract") not in {CONTRACT, CONTRACT_V2}:
        raise BuildInputError("BUILD_SELECTION_SCHEMA")
    if v2 and _version(value["target_version"]) <= _version(value["predecessor_version"]):
        raise BuildInputError("BUILD_SELECTION_VERSION_ORDER")
    start = date.fromisoformat(value["predecessor_through"])
    end = date.fromisoformat(value["through_date"])
    days = value["expected_days"]
    if (start >= end or not isinstance(days, list) or not days
            or days != sorted(set(days))
            or any(not start < date.fromisoformat(day) <= end for day in days)):
        raise BuildInputError("BUILD_SELECTION_PERIOD")
    packages = value["packages"]
    if not isinstance(packages, list) or len(packages) != len(days) + 2:
        raise BuildInputError("BUILD_SELECTION_PACKAGES")
    ids: set[str] = set()
    daily = []
    bases = []
    for row in packages:
        if (not isinstance(row, dict) or set(row) != {"id", "role", "manifest_sha256", "day"}
                or not isinstance(row["id"], str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", row["id"])
                or row["id"] in ids or row["role"] not in KINDS
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", row["manifest_sha256"])):
            raise BuildInputError("BUILD_SELECTION_PACKAGE_IDENTITY")
        ids.add(row["id"])
        if row["role"] == "daily":
            daily.append(row["day"])
        else:
            bases.append(row["role"])
            if row["day"] is not None:
                raise BuildInputError("BUILD_SELECTION_BASE_DAY")
    if sorted(bases) != ["dwd", "ods"] or sorted(daily) != days:
        raise BuildInputError("BUILD_SELECTION_DAY_OR_BASE_SET")
    if v2:
        if not isinstance(value["evidence"], list):
            raise BuildInputError("BUILD_SELECTION_EVIDENCE_SCHEMA")
        parent_identities = {row["manifest_sha256"] for row in packages}
        for entry in value["evidence"]:
            if (not isinstance(entry, dict)
                    or set(entry) != {"id", "purpose", "parent_manifests", "files"}
                    or not isinstance(entry["id"], str)
                    or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", entry["id"])
                    or entry["id"] in ids or entry["purpose"] not in EVIDENCE_PURPOSES
                    or not isinstance(entry["parent_manifests"], list)
                    or not entry["parent_manifests"]
                    or not isinstance(entry["files"], list) or not entry["files"]):
                raise BuildInputError("BUILD_SELECTION_EVIDENCE_SCHEMA")
            ids.add(entry["id"])
            # External parent metadata must itself be retained and hash-selected.
            available = parent_identities | {
                row.get("sha256") for row in entry["files"] if isinstance(row, dict)
            }
            for parent in entry["parent_manifests"]:
                if (not isinstance(parent, str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", parent)
                        or parent not in available):
                    raise BuildInputError("BUILD_SELECTION_EVIDENCE_PARENT_NOT_RETAINED")
    evidence = value["expected_days_evidence"]
    if (not isinstance(evidence, dict)
            or set(evidence) != {"package_id", "path", "sha256"}
            or evidence["package_id"] not in ids
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence["sha256"])):
        raise BuildInputError("BUILD_SELECTION_CALENDAR_EVIDENCE")


def freeze(selection: dict[str, Any], roots: dict[str, str], *, workers: int = 4
           ) -> dict[str, Any]:
    validate_selection(selection)
    ids = {row["id"] for row in selection["packages"]}
    v2 = selection["contract"] == CONTRACT_V2
    if v2:
        ids |= {row["id"] for row in selection["evidence"]}
    if set(roots) != ids:
        raise BuildInputError("BUILD_SELECTION_ROOT_BINDINGS")
    results = {}
    for row in selection["packages"]:
        root = Path(roots[row["id"]])
        result = inventory(root, row["manifest_sha256"], workers=workers)
        manifest = load(root / "manifest.json")
        kind = result["content"]["package_kind"]
        allowed = {KINDS[row["role"]]}
        if v2:
            allowed.add(GENERIC_KINDS[row["role"]])
        if kind not in allowed:
            raise BuildInputError("BUILD_SELECTION_ROLE_KIND")
        generic = kind in GENERIC_KINDS.values()
        metadata = manifest["content"] if generic else manifest
        if generic and metadata.get("role") != row["role"]:
            raise BuildInputError("BUILD_SELECTION_ROLE_KIND")
        if row["role"] == "daily":
            if metadata.get("market_date") != row["day"]:
                raise BuildInputError("BUILD_SELECTION_DAILY_DATE")
        else:
            through = (metadata.get("through_date") if generic else
                       manifest.get("business_through") if row["role"] == "dwd"
                       else manifest.get("content", {}).get("through_date"))
            if through != selection["predecessor_through"]:
                raise BuildInputError("BUILD_SELECTION_PREDECESSOR_DATE")
            if v2:
                actual_version = metadata.get("base_version") if generic else "2.4"
                if actual_version != selection["predecessor_version"]:
                    raise BuildInputError("BUILD_SELECTION_PREDECESSOR_VERSION")
        results[row["id"]] = result
    if v2:
        for row in selection["evidence"]:
            results[row["id"]] = inventory_evidence(
                Path(roots[row["id"]]), row["files"], workers=workers,
            )
    evidence = selection["expected_days_evidence"]
    files = results[evidence["package_id"]]["content"]["files"]
    if not any(f["path"] == evidence["path"] and f["sha256"] == evidence["sha256"]
               for f in files):
        raise BuildInputError("BUILD_SELECTION_CALENDAR_NOT_IN_CLOSURE")
    # Calendar semantic coverage and native/data quality belong to the next audit
    # stage. Byte inventory deliberately cannot imply those gates passed.
    content = {"contract": _frozen_contract(selection), "selection": selection,
               "packages": results}
    return {"content": content, "identity": identity(content),
            "status": "FROZEN_BYTES_ONLY_SEMANTIC_GATES_PENDING"}


def cold_verify(frozen: dict[str, Any], roots: dict[str, str], *, workers: int = 4
                ) -> dict[str, Any]:
    content = frozen.get("content", {})
    if (frozen.get("identity") != identity(content)
            or content.get("contract") not in {FROZEN, FROZEN_V2}):
        raise BuildInputError("BUILD_FROZEN_IDENTITY")
    validate_selection(content["selection"])
    if content["contract"] != _frozen_contract(content["selection"]):
        raise BuildInputError("BUILD_FROZEN_IDENTITY")
    if set(roots) != set(content["packages"]):
        raise BuildInputError("BUILD_SELECTION_ROOT_BINDINGS")
    # freeze performs one full physical/hash pass per package. Its complete
    # result must equal the frozen inventory; do not hash the same inputs twice.
    actual = freeze(content["selection"], roots, workers=workers)
    if actual != frozen:
        raise BuildInputError("BUILD_FROZEN_SELECTION_CHANGED")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "verify"])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--roots", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    roots = load(args.roots)
    # An audit receipt may not be placed inside an authoritative package.
    destination = args.output.absolute()
    for root in roots.values():
        source = Path(root).absolute()
        if source == destination or source in destination.parents:
            raise BuildInputError("BUILD_RECEIPT_INSIDE_SOURCE")
    result = (freeze(load(args.input), roots, workers=args.workers)
              if args.action == "freeze"
              else cold_verify(load(args.input), roots, workers=args.workers))
    with args.output.open("xb") as stream:
        stream.write(canonical(result) + b"\n")
    print(json.dumps({"status": result["status"], "identity": result["identity"]}))


if __name__ == "__main__":
    main()
