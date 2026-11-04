"""Readonly paired audit anchored in archived inputs and exact predecessor ownership.

Physical guards only reject observed inode/metadata changes early; matching
metadata is never byte verification or authority to reuse a stale audit.
Historical disposition and independent C remain separate
explicit gates; a structural receipt is never full Base acceptance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError, Budget
from .base23_native_image import image_schemas
from .base_build_composition import audit_standard_image
from .base_build_inputs import _fingerprint, _tree, canonical, file_sha, identity, member
from .base_build_ods import audit_ods
from .base_build_period import PARENTS, VerifiedInputs, load, prepare_period
from .base_build_profile import REFERENCE_TABLES, UPDATED_TABLES

CONTRACT = "p10-standard-base-pair-audit-v1"


def physical_guard(root: Path) -> dict[str, Any]:
    """Hash independent file fingerprints; never substitute this for a byte audit."""
    h, size, count = hashlib.sha256(), 0, 0
    for relative in sorted(_tree(root) - {"manifest.json", "READY"}):
        stamp = _fingerprint(member(root, relative))
        h.update(canonical([relative, *stamp]) + b"\n")
        size += stamp[2]
        count += 1
    return {"files": count, "bytes": size, "physical_identity": "sha256:" + h.hexdigest()}


def _same_file(left: Path, right: Path) -> None:
    if file_sha(left) != file_sha(right):
        raise BoundedError("BUILD_PAIR_AUDIT_SHARED_EVIDENCE_DIFFERS")


def _closure(
    *, ods: Path, dwd: Path, binding: dict[str, Any], budget: Budget
) -> tuple[dict[str, Path], dict[str, Any]]:
    """Prove complete file ownership and update/inherit policy, using local bytes.

    The layer auditors already checked every ODS member and every DWD byte.
    This projection pins all original DWD files to one of those verified copies.
    Empty predecessor files removed by composition are retained in ODS.
    """
    archive = load(member(ods, "MATERIALIZATION.json"))
    current = load(member(dwd, "MATERIALIZATION.json"))
    plan = load(member(dwd, "evidence/input-plan.json"))
    frozen = load(member(ods, "evidence/frozen-inputs.json"))
    prior_id = next(k for k, v in archive["sources"].items() if v["role"] == "dwd")
    prior_root = member(ods, archive["sources"][prior_id]["prefix"])
    prior_record = load(member(prior_root, "MATERIALIZATION.json"))
    prior_plan = load(member(prior_root, "evidence/input-plan.json"))
    prior_manifest = load(member(prior_root, "manifest.json"))
    period = load(member(dwd, "evidence/sources/period.json"))
    admission = load(member(ods, "evidence/period/ADMISSION.json"))
    if (
        plan.get("build_binding") != binding
        or admission.get("binding") != binding
        or binding["frozen_input_identity"] != frozen["identity"]
        or file_sha(prior_root / "manifest.json") != binding["predecessor_manifest_sha256"]
        or prior_manifest.get("materialization_sha256")
        != file_sha(prior_root / "MATERIALIZATION.json")
        or prior_record.get("plan_digest") != digest(prior_plan)
        or plan["sources"]["predecessor"]["descriptor_sha256"]
        != file_sha(prior_root / "MATERIALIZATION.json")
        or period.get("identity") != identity({k: v for k, v in period.items() if k != "identity"})
        or period.get("identity") != admission.get("delta_identity")
        or period.get("binding") != binding
        or set(period["tables"]) not in (UPDATED_TABLES, UPDATED_TABLES | REFERENCE_TABLES)
        or set(current["layout"]) != set(prior_record["layout"])
    ):
        raise BoundedError("BUILD_PAIR_AUDIT_SOURCE_OR_SCOPE_BINDING_CHANGED")
    _same_file(dwd / "evidence/sources/predecessor.json", prior_root / "MATERIALIZATION.json")
    for name in ("manifest.json", "MATERIALIZATION.json", *prior_record["evidence"]):
        _same_file(dwd / ("evidence/predecessor-control/" + name), prior_root / name)
    # The successor registry is serialized by the native writer. Its values
    # must be identical; the original registry bytes are retained and checked
    # above under predecessor-control and in the ODS archive.
    if json.loads((dwd / "evidence/limitations.json").read_bytes()) != json.loads(
        (prior_root / "evidence/limitations.json").read_bytes()
    ):
        raise BoundedError("BUILD_PAIR_AUDIT_INHERITED_LIMITATION_VALUES_CHANGED")
    for name in ("ADMISSION.json", "CONFIG.json"):
        _same_file(dwd / ("evidence/build/" + name), ods / ("evidence/period/" + name))
    for name in ("PLAN.json", "CHECKPOINT.json", "WINDOWS.parquet"):
        _same_file(dwd / ("evidence/build/windows/" + name), ods / ("evidence/windows/" + name))
    _same_file(dwd / "evidence/build/ODS-MATERIALIZATION.json", ods / "MATERIALIZATION.json")
    period_files = {t: [] for t in period["tables"]}
    for row in period["files"]:
        if row["table"] not in period_files:
            raise BoundedError("BUILD_PAIR_AUDIT_PERIOD_TABLE_SET_CHANGED")
        period_files[row["table"]].append(row)
    mapped = {}
    table_counts = {}
    fields = ("path", "table", "partition", "rows", "size_bytes", "sha256")
    for table, original in sorted(prior_record["layout"].items()):
        budget.check()
        expected_old = [
            dict({k: r[k] for k in fields}, source_id="predecessor") for r in original["files"]
        ]
        added = [dict(r, source_id="period") for r in period_files.get(table, [])]
        candidates = expected_old + added
        positive = [r for r in candidates if r["rows"] > 0]
        expected = positive or [candidates[0]]
        if plan["tables"][table]["files"] != expected:
            raise BoundedError("BUILD_PAIR_AUDIT_INHERIT_OR_PERIOD_MEMBER_CLOSURE_CHANGED")
        expected_disposition = {
            "action": "APPEND_ADMITTED_PERIOD"
            if table in UPDATED_TABLES
            else "APPEND_SOURCE_REPLAYED_IDENTITY_EXTENSION"
            if table in period_files
            else "INHERIT",
            "predecessor_rows": sum(r["rows"] for r in expected_old),
            "period_rows": sum(r["rows"] for r in added),
            "predecessor_through": binding["predecessor_through"],
            "coverage_statement": "ORIGINAL_COVERAGE_RETAINED_NO_REFRESH_INFERRED",
        }
        if plan["table_dispositions"][table] != expected_disposition:
            raise BoundedError("BUILD_PAIR_AUDIT_TABLE_DISPOSITION_CHANGED")
        if (
            table in period["tables"]
            and sum(r["rows"] for r in added) != period["tables"][table]["output_rows"]
        ):
            raise BoundedError("BUILD_PAIR_AUDIT_PERIOD_ROW_CENSUS_CHANGED")
        for row in current["layout"][table]["files"]:
            if row["source_id"] != "predecessor":
                continue
            key = row["archive_source_path"]
            if key in mapped:
                raise BoundedError("BUILD_PAIR_AUDIT_PREDECESSOR_FILE_DUPLICATE")
            mapped[key] = member(dwd, row["path"])
        for old in original["files"]:
            if old["rows"] == 0 and old["path"] not in mapped:
                mapped[old["path"]] = member(prior_root, old["path"])
        table_counts[table] = {
            "predecessor_rows": expected_disposition["predecessor_rows"],
            "period_rows": expected_disposition["period_rows"],
            "output_rows": sum(r["rows"] for r in expected),
        }
    frozen_files = frozen["content"]["packages"][prior_id]["content"]["files"]
    originals = {r["path"]: r for t in prior_record["layout"].values() for r in t["files"]}
    if set(mapped) != set(originals):
        raise BoundedError("BUILD_PAIR_AUDIT_PREDECESSOR_PAYLOAD_OWNERSHIP_CHANGED")
    for row in frozen_files:
        relative = row["path"]
        if relative.startswith("payload/"):
            original = originals.get(relative, {})
            if (
                original.get("sha256") != row["sha256"]
                or original.get("size_bytes") != row["size_bytes"]
                or mapped[relative].stat().st_size != row["size_bytes"]
            ):
                raise BoundedError("BUILD_PAIR_AUDIT_ORIGINAL_PAYLOAD_NOT_FROZEN")
        elif not member(prior_root, relative).is_file():
            raise BoundedError("BUILD_PAIR_AUDIT_ORIGINAL_CONTROL_NOT_ARCHIVED")
    if set(originals) != {r["path"] for r in frozen_files if r["path"].startswith("payload/")}:
        raise BoundedError("BUILD_PAIR_AUDIT_PREDECESSOR_PAYLOAD_INVENTORY_CHANGED")
    references = {
        r["path"]: mapped[r["path"]] for t in PARENTS for r in prior_record["layout"][t]["files"]
    }
    return references, {
        "predecessor_id": prior_id,
        "table_counts": table_counts,
        "predecessor_files": len(mapped),
        "predecessor_bytes": sum(r["size_bytes"] for r in frozen_files),
    }


def _audit_structure(
    *, ods: Path, dwd: Path, contract: dict[str, Any], expected_input_identity: str, budget: Budget
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Full layer scans and paired closure; source replay remains explicit below."""
    before = {"ods": physical_guard(ods), "dwd": physical_guard(dwd)}
    ods_receipt = audit_ods(ods, budget, expected_input_identity=expected_input_identity)
    dwd_receipt = audit_standard_image(dwd, budget, contract)
    admission = load(member(ods, "evidence/period/ADMISSION.json"))
    references, closure = _closure(ods=ods, dwd=dwd, binding=admission["binding"], budget=budget)
    after = {"ods": physical_guard(ods), "dwd": physical_guard(dwd)}
    if before != after:
        raise BoundedError("BUILD_PAIR_AUDIT_PRODUCTS_CHANGED_DURING_SCAN")
    result = {
        "contract": CONTRACT,
        "input_identity": expected_input_identity,
        "binding": admission["binding"],
        "ODS": ods_receipt,
        "DWD": dwd_receipt,
        "physical_guard": after,
        "closure": closure,
        "source_replay": "PENDING",
        "historical_disposition": "PENDING",
        "publication_eligible": False,
        "full_A_B_C": False,
    }
    result["identity"] = identity(result)
    return result, references


def audit_structure(
    *, ods: Path, dwd: Path, contract: dict[str, Any], expected_input_identity: str, budget: Budget
) -> dict[str, Any]:
    result, _ = _audit_structure(
        ods=ods,
        dwd=dwd,
        contract=contract,
        expected_input_identity=expected_input_identity,
        budget=budget,
    )
    return result


def audit_pair(
    *, ods: Path, dwd: Path, contract: dict[str, Any], expected_input_identity: str, budget: Budget
) -> dict[str, Any]:
    """Cold source replay from paired products; never open original input roots."""
    result, references = _audit_structure(
        ods=ods,
        dwd=dwd,
        contract=contract,
        expected_input_identity=expected_input_identity,
        budget=budget,
    )
    frozen = load(member(ods, "evidence/frozen-inputs.json"))
    archive = load(member(ods, "MATERIALIZATION.json"))
    roots = {sid: str(member(ods, r["prefix"])) for sid, r in archive["sources"].items()}
    # These are fresh full byte audits above, not a persisted receipt/cache hit.
    # Every original DWD byte is either in ODS or the pinned native DWD copy.
    inputs = VerifiedInputs(frozen, roots, expected_input_identity, references)
    rules = load(member(dwd, "contract/base22-independent-image.v1.json"))
    schemas = image_schemas(
        load(member(dwd, "contract/dwd-domain-model.v1.json")),
        load(member(dwd, "contract/dwd-domain-model.v1.1-extension.json")),
    )
    configuration = load(member(ods, "evidence/period/CONFIG.json"))
    if set(configuration) != {"source_evidence_id", "closed_days", "calendar_reference"}:
        raise BoundedError("BUILD_PAIR_AUDIT_SOURCE_CONFIGURATION_INVALID")
    replay = prepare_period(
        inputs=inputs,
        binding=result["binding"],
        rules=rules,
        schemas=schemas,
        output=budget.root / "source-replay",
        budget=budget,
        **configuration,
    )
    original = load(member(ods, "evidence/period/ADMISSION.json"))
    if replay != original:
        raise BoundedError("BUILD_PAIR_AUDIT_SOURCE_REPLAY_DIFFERS")
    if {"ods": physical_guard(ods), "dwd": physical_guard(dwd)} != result["physical_guard"]:
        raise BoundedError("BUILD_PAIR_AUDIT_PRODUCTS_CHANGED_DURING_REPLAY")
    result.update(
        source_replay="PASS_ALL_PERIOD_SOURCES_AND_TYPED_DELTA",
        replay_identity=replay["identity"],
        source_replay_bytes=sum(
            p.stat().st_size for p in (budget.root / "source-replay").rglob("*") if p.is_file()
        ),
    )
    result["identity"] = identity({k: v for k, v in result.items() if k != "identity"})
    return result
