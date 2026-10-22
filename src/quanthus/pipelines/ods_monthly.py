"""Independent monthly ODS raw compaction and offline native verification.

Original source revisions remain exact physical files. The versioned daily-purpose
contract names unavailable uses; frozen July policies and CURRENT are untouched.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.ods_base import _parquet_metadata, _sqlite_metadata
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _fingerprint,
    _member,
    _tree,
    disjoint_output,
    physical_path,
)

PURPOSE = "CURRENT_DAILY_GOVERNANCE_RECONSTRUCTION_SEED"
ROLES = {"historical_seed", "core", "events", "weekly", "intraday_1m", "intraday_5m"}


class MonthlyOdsError(RuntimeError):
    """Safe reason codes only; never include row values or source JSON bodies."""


def _manifest(source: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    root = physical_path(Path(source["root"]))
    name = source["manifest_name"]
    p = _member(root, name)
    if file_sha(p) != source["manifest_sha256"]:
        raise MonthlyOdsError("MONTHLY_ODS_FROZEN_MANIFEST_CHANGED")
    if _member(root, "READY").read_text().strip() != file_sha(p):
        raise MonthlyOdsError("MONTHLY_ODS_ORIGINAL_READY_CHANGED")
    m = json.loads(p.read_bytes())
    return m, root


def freeze_monthly_plan(selection: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Freeze exact raw revisions and physical paths before any package write."""
    if contract.get("purpose") != PURPOSE or contract.get("business_timezone") != "Asia/Shanghai":
        raise MonthlyOdsError("MONTHLY_ODS_CONTRACT_PURPOSE_CHANGED")
    if contract["required_daily_roles"] != ["core", "events"]:
        raise MonthlyOdsError("MONTHLY_ODS_REQUIRED_ROLES_CHANGED")
    through = date.fromisoformat(contract["business_through"])
    dates = contract["calendar_authority"]["open_dates"]
    if len(set(dates)) != len(dates) or any(date.fromisoformat(d) > through for d in dates):
        raise MonthlyOdsError("MONTHLY_ODS_CALENDAR_SCOPE_CHANGED")
    sources = selection["selected"]
    admitted = contract["admitted_sources"]
    if {s["manifest_sha256"] for s in sources} != set(admitted):
        raise MonthlyOdsError("MONTHLY_ODS_UNQUALIFIED_SOURCE")
    if any(s["role"] != admitted[s["manifest_sha256"]]["role"] for s in sources):
        raise MonthlyOdsError("MONTHLY_ODS_SOURCE_ROLE_CHANGED")
    if sum(s["role"] == "historical_seed" for s in sources) != 1:
        raise MonthlyOdsError("MONTHLY_ODS_EXACT_SEED_REQUIRED")
    if len({s["manifest_sha256"] for s in sources}) != len(sources):
        raise MonthlyOdsError("MONTHLY_ODS_SOURCE_IDENTITY_REPEATED")
    members = []
    descriptors = []
    covered: dict[str, set[str]] = {r: set() for r in contract["required_daily_roles"]}
    targets = set()
    for source in sources:
        role = source["role"]
        if role not in ROLES:
            raise MonthlyOdsError("MONTHLY_ODS_UNKNOWN_ROLE")
        m, root = _manifest(source)
        sid = source["manifest_sha256"].removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-f]{64}", sid) is None:
            raise MonthlyOdsError("MONTHLY_ODS_INVALID_SOURCE_IDENTITY")
        if role == "historical_seed":
            if m.get("kind") != "quanthus_ods_base_bundle" or m.get("schema_version") != 1:
                raise MonthlyOdsError("MONTHLY_ODS_SEED_KIND_CHANGED")
            if digest(m["content"]) != m["content_digest"]:
                raise MonthlyOdsError("MONTHLY_ODS_SEED_CONTENT_CHANGED")
            if source["manifest_sha256"] != contract["historical_seed"]["manifest_sha256"]:
                raise MonthlyOdsError("MONTHLY_ODS_SEED_AUTHORITY_CHANGED")
            records = m["content"]["files"]
            business_date = m["content"]["through_date"]
            if date.fromisoformat(business_date) > through:
                raise MonthlyOdsError("MONTHLY_ODS_BUSINESS_OBSERVATION_AFTER_CUTOFF")
        else:
            if m.get("status") != "ready" or m.get("release_id") != source["identity"]:
                raise MonthlyOdsError("MONTHLY_ODS_ORIGINAL_RELEASE_CHANGED")
            business_date = m["market_date"]
            if date.fromisoformat(business_date) > through:
                raise MonthlyOdsError("MONTHLY_ODS_BUSINESS_OBSERVATION_AFTER_CUTOFF")
            records = [{**a, "path": a["relative_path"]} for a in m["artifacts"]]
            names = {a["name"] for a in m["artifacts"]}
            if role in covered and set(contract["required_artifacts"][role]) <= names:
                covered[role].add(business_date)
        physical = {r["path"] for r in records} | {source["manifest_name"], "READY"}
        if len(records) != len({r["path"] for r in records}) or _tree(root) != physical:
            raise MonthlyOdsError("MONTHLY_ODS_ORIGINAL_FILE_SET_CHANGED")
        descriptor = {
            "source_id": sid,
            "role": role,
            "manifest_name": source["manifest_name"],
            "manifest_sha256": source["manifest_sha256"],
            "business_date": business_date,
            "archive_source_root": str(root),
            "members": [],
        }
        for r in records:
            original = _member(root, r["path"])
            target = (
                r["path"]
                if role == "historical_seed"
                else f"payload/increments/{role}/{sid}/{r['path']}"
            )
            _member(Path("/placeholder-monthly-ods"), target)
            if target in targets or not target.startswith("payload/"):
                raise MonthlyOdsError("MONTHLY_ODS_TARGET_COLLISION")
            targets.add(target)
            item = {
                "path": target,
                "source_id": sid,
                "original_path": r["path"],
                "archive_source_path": str(original),
                "size_bytes": r["size_bytes"],
                "sha256": r["sha256"],
                "source_fingerprint": list(_fingerprint(original)),
                "role": role,
                "format": "parquet"
                if original.suffix == ".parquet"
                else "sqlite"
                if original.suffix == ".db"
                else "raw",
            }
            if item["format"] == "parquet":
                item["metadata"] = _parquet_metadata(original, tuple(r.get("date_bounds", {})))
            members.append(item)
            descriptor["members"].append(
                {k: item[k] for k in ("path", "original_path", "size_bytes", "sha256")}
            )
        for name in [source["manifest_name"], "READY"]:
            original = _member(root, name)
            target = f"payload/source-manifests/{sid}/{name}"
            if target in targets:
                raise MonthlyOdsError("MONTHLY_ODS_TARGET_COLLISION")
            targets.add(target)
            members.append(
                {
                    "path": target,
                    "source_id": sid,
                    "original_path": name,
                    "archive_source_path": str(original),
                    "size_bytes": original.stat().st_size,
                    "sha256": file_sha(original),
                    "source_fingerprint": list(_fingerprint(original)),
                    "role": "original_manifest_evidence",
                    "format": "raw",
                }
            )
        descriptors.append(descriptor)
    for role, present in covered.items():
        if not set(dates) <= present:
            raise MonthlyOdsError("MONTHLY_ODS_REQUIRED_DAILY_ROLE_MISSING:" + role)
    result = {
        "purpose": PURPOSE,
        "contract": copy.deepcopy(contract),
        "contract_sha256": digest(contract),
        "sources": descriptors,
        "files": members,
        "business_through": through.isoformat(),
        "READY": False,
        "source_mutation": False,
    }
    result["identity"] = digest(result)
    return result


def _check_image_write_space(output: Path, budget: Any, reserve: int) -> None:
    # Budget owns audit TMP, not a separately registered physical image. Keep its
    # memory/temp/time checks and reserve image bytes on the actual output device.
    budget.check()
    existing = output
    while not existing.exists():
        existing = existing.parent
    fs = os.statvfs(existing)
    floor = getattr(getattr(budget, "limits", None), "free_floor_bytes", 0)
    if fs.f_bavail * fs.f_frsize - reserve < floor:
        raise MonthlyOdsError("MONTHLY_ODS_OUTPUT_FREE_FLOOR")


def materialize_monthly(plan: dict[str, Any], output: Path, budget: Any) -> dict[str, Any]:
    """Only after task-specific capacity passes; independently copy original bytes."""
    sources = [Path(s["archive_source_root"]) for s in plan["sources"]]
    output = disjoint_output(output, sources)
    disjoint_output(output.parent / ("." + output.name + ".job"), sources)
    if output.exists():
        raise FileExistsError("MONTHLY_ODS_OUTPUT_ALREADY_EXISTS")
    if digest({k: v for k, v in plan.items() if k != "identity"}) != plan["identity"]:
        raise MonthlyOdsError("MONTHLY_ODS_PLAN_CHANGED")
    _check_image_write_space(
        output, budget, sum(f["size_bytes"] for f in plan["files"]) + len(canonical(plan)) + 4096
    )
    for source in plan["sources"]:
        expected = {m["original_path"] for m in source["members"]} | {
            source["manifest_name"],
            "READY",
        }
        if _tree(physical_path(Path(source["archive_source_root"]))) != expected:
            raise MonthlyOdsError("MONTHLY_ODS_INPUT_NAMESPACE_CHANGED_BEFORE_WRITE")
    for f in plan["files"]:
        if (
            list(_fingerprint(physical_path(Path(f["archive_source_path"]))))
            != f["source_fingerprint"]
        ):
            raise MonthlyOdsError("MONTHLY_ODS_INPUT_CHANGED_BEFORE_WRITE")
    output.mkdir()
    budget.begin_stage("independent-raw-monthly-compaction")
    for item in plan["files"]:
        p = physical_path(Path(item["archive_source_path"]))
        q = _member(output, item["path"])
        _check_image_write_space(output, budget, item["size_bytes"])
        q.parent.mkdir(parents=True, exist_ok=True)
        with p.open("rb") as src, q.open("xb") as dst:
            copied = 0
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                copied += len(chunk)
                if copied > item["size_bytes"]:
                    raise MonthlyOdsError("MONTHLY_ODS_SOURCE_SIZE_EXCEEDED")
                _check_image_write_space(output, budget, len(chunk))
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if file_sha(q) != item["sha256"] or q.stat().st_size != item["size_bytes"]:
            raise MonthlyOdsError("MONTHLY_ODS_COPIED_BYTES_CHANGED")
        if list(_fingerprint(p)) != item["source_fingerprint"]:
            raise MonthlyOdsError("MONTHLY_ODS_SOURCE_CHANGED_DURING_COPY")
    for source in plan["sources"]:
        expected = {m["original_path"] for m in source["members"]} | {
            source["manifest_name"],
            "READY",
        }
        if _tree(physical_path(Path(source["archive_source_root"]))) != expected:
            raise MonthlyOdsError("MONTHLY_ODS_INPUT_NAMESPACE_CHANGED_DURING_COPY")
    for item in plan["files"]:
        if (
            list(_fingerprint(physical_path(Path(item["archive_source_path"]))))
            != item["source_fingerprint"]
        ):
            raise MonthlyOdsError("MONTHLY_ODS_SOURCE_CHANGED_DURING_COPY")
    materialization = {
        **copy.deepcopy(plan),
        "frozen_plan_identity": plan["identity"],
        "build_UTC": datetime.now(timezone.utc).isoformat(),
        "READY": False,
    }
    materialization["identity"] = digest(
        {k: v for k, v in materialization.items() if k != "identity"}
    )
    encoded = canonical(materialization) + b"\n"
    _check_image_write_space(output, budget, len(encoded))
    with (output / "MATERIALIZATION.json").open("xb") as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
    return audit_monthly(output, budget, expected_contract_digest=plan["contract_sha256"])


def _native_parquet_census(path: Path, budget: Any) -> dict[str, Any]:
    """Decode all typed fields without joining wide original row groups."""
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    typed = hashlib.sha256()
    seen_rows = max_batch_rows = max_batch_bytes = 0
    with pq.ParquetFile(path, pre_buffer=False, buffer_size=65536) as reader:
        expected_rows = reader.metadata.num_rows
        typed.update(reader.schema_arrow.serialize().to_pybytes())
        for group in range(reader.num_row_groups):
            for batch in reader.iter_batches(
                batch_size=16384, row_groups=[group], use_threads=False
            ):
                budget.check()
                sink = pa.BufferOutputStream()
                with pa.ipc.new_stream(sink, batch.schema) as writer:
                    writer.write_batch(batch)
                typed.update(sink.getvalue())
                seen_rows += batch.num_rows
                max_batch_rows = max(max_batch_rows, batch.num_rows)
                max_batch_bytes = max(max_batch_bytes, batch.nbytes)
                budget.check()
    if seen_rows != expected_rows:
        raise MonthlyOdsError("MONTHLY_ODS_FULL_NATIVE_ROW_CENSUS_CHANGED")
    return {
        "rows": seen_rows,
        "typed_ipc_sha256": "sha256:" + typed.hexdigest(),
        "batch_boundary": "original_row_group",
        "max_batch_rows": max_batch_rows,
        "max_batch_bytes": max_batch_bytes,
    }


def audit_monthly(root: Path, budget: Any, *, expected_contract_digest: str) -> dict[str, Any]:
    """Offline raw-byte/schema and original-manifest closure; no source-root access."""
    root = physical_path(root)
    sealed = (root / "manifest.json").exists() or (root / "READY").exists()
    envelope = None
    plan_name = "payload/source-plan/MATERIALIZATION.json" if sealed else "MATERIALIZATION.json"
    if sealed:
        envelope = json.loads(_member(root, "manifest.json").read_bytes())
        if (
            envelope.get("schema_version") != 1
            or envelope.get("kind") != "quanthus_ods_base_bundle"
            or digest(envelope["content"]) != envelope.get("content_digest")
            or _member(root, "READY").read_text().strip() != file_sha(root / "manifest.json")
        ):
            raise MonthlyOdsError("MONTHLY_ODS_SEALED_ENVELOPE_CHANGED")
    plan = json.loads(_member(root, plan_name).read_bytes())
    if plan.get("purpose") != PURPOSE or plan.get("READY") is not False:
        raise MonthlyOdsError("MONTHLY_ODS_PLAN_PURPOSE_CHANGED")
    if digest({k: v for k, v in plan.items() if k != "identity"}) != plan["identity"]:
        raise MonthlyOdsError("MONTHLY_ODS_PLAN_CHANGED")
    frozen = {
        k: v for k, v in plan.items() if k not in ("identity", "build_UTC", "frozen_plan_identity")
    }
    if digest(frozen) != plan["frozen_plan_identity"]:
        raise MonthlyOdsError("MONTHLY_ODS_FROZEN_PLAN_CHANGED")
    if (
        digest(plan["contract"]) != plan["contract_sha256"]
        or plan["contract_sha256"] != expected_contract_digest
    ):
        raise MonthlyOdsError("MONTHLY_ODS_CONTRACT_CHANGED")
    contract = plan["contract"]
    if contract.get("purpose") != PURPOSE or contract.get("business_timezone") != "Asia/Shanghai":
        raise MonthlyOdsError("MONTHLY_ODS_CONTRACT_PURPOSE_CHANGED")
    if contract["required_daily_roles"] != ["core", "events"]:
        raise MonthlyOdsError("MONTHLY_ODS_REQUIRED_ROLES_CHANGED")
    if plan["business_through"] != contract["business_through"]:
        raise MonthlyOdsError("MONTHLY_ODS_CUTOFF_CHANGED")
    expected = {f["path"] for f in plan["files"]}
    extra = {plan_name, "manifest.json", "READY"} if sealed else {plan_name}
    if len(expected) != len(plan["files"]) or _tree(root) != expected | extra:
        raise MonthlyOdsError("MONTHLY_ODS_PHYSICAL_FILE_SET_CHANGED")
    if envelope is not None:
        content = envelope["content"]
        if (
            content.get("governance_purpose") != PURPOSE
            or content.get("through_date") != plan["business_through"]
            or content.get("policy_sha256") != expected_contract_digest
            or content.get("source_inventory") != plan["sources"]
            or content.get("time_and_use_contract") != plan["contract"]
            or content.get("scope_gaps") != plan["contract"]["intraday"]["availability_gaps"]
        ):
            raise MonthlyOdsError("MONTHLY_ODS_SEALED_SCOPE_CHANGED")
        actual = {f["path"]: (f["size_bytes"], f["sha256"]) for f in content["files"]}
        wanted = {f["path"]: (f["size_bytes"], f["sha256"]) for f in plan["files"]}
        wanted[plan_name] = ((root / plan_name).stat().st_size, file_sha(root / plan_name))
        if len(actual) != len(content["files"]) or actual != wanted:
            raise MonthlyOdsError("MONTHLY_ODS_SEALED_MEMBER_CLOSURE_CHANGED")
    budget.begin_stage("offline-raw-monthly-compaction-audit")
    if (
        len({s["source_id"] for s in plan["sources"]}) != len(plan["sources"])
        or sum(s["role"] == "historical_seed" for s in plan["sources"]) != 1
    ):
        raise MonthlyOdsError("MONTHLY_ODS_SOURCE_IDENTITY_REPEATED")
    admitted = contract["admitted_sources"]
    if {s["manifest_sha256"] for s in plan["sources"]} != set(admitted) or any(
        s["role"] != admitted.get(s["manifest_sha256"], {}).get("role") for s in plan["sources"]
    ):
        raise MonthlyOdsError("MONTHLY_ODS_UNQUALIFIED_SOURCE")
    files_by_path = {f["path"]: f for f in plan["files"]}
    independently_expected = set()
    covered: dict[str, set[str]] = {r: set() for r in contract["required_daily_roles"]}
    for s in plan["sources"]:
        if s["role"] not in ROLES or s["source_id"] != s["manifest_sha256"].removeprefix("sha256:"):
            raise MonthlyOdsError("MONTHLY_ODS_UNKNOWN_ROLE_OR_SOURCE")
        base = f"payload/source-manifests/{s['source_id']}/"
        p = _member(root, base + s["manifest_name"])
        if file_sha(p) != s["manifest_sha256"] or _member(
            root, base + "READY"
        ).read_text().strip() != file_sha(p):
            raise MonthlyOdsError("MONTHLY_ODS_LOCAL_ORIGINAL_MANIFEST_CHANGED")
        m = json.loads(p.read_bytes())
        if s["role"] == "historical_seed":
            if s["manifest_sha256"] != contract["historical_seed"]["manifest_sha256"] or (
                m.get("kind") != "quanthus_ods_base_bundle"
                or digest(m["content"]) != m["content_digest"]
            ):
                raise MonthlyOdsError("MONTHLY_ODS_SEED_AUTHORITY_CHANGED")
            business_date = m["content"]["through_date"]
        else:
            if m.get("status") != "ready":
                raise MonthlyOdsError("MONTHLY_ODS_ORIGINAL_RELEASE_CHANGED")
            business_date = m["market_date"]
            if s["role"] in covered and set(contract["required_artifacts"][s["role"]]) <= {
                a["name"] for a in m["artifacts"]
            }:
                covered[s["role"]].add(business_date)
        if business_date != s["business_date"] or date.fromisoformat(
            business_date
        ) > date.fromisoformat(plan["business_through"]):
            raise MonthlyOdsError("MONTHLY_ODS_BUSINESS_OBSERVATION_AFTER_CUTOFF")
        original = (
            m["content"]["files"]
            if s["role"] == "historical_seed"
            else [{**a, "path": a["relative_path"]} for a in m["artifacts"]]
        )
        if sorted((f["path"], f["sha256"], f["size_bytes"]) for f in original) != sorted(
            (f["original_path"], f["sha256"], f["size_bytes"]) for f in s["members"]
        ):
            raise MonthlyOdsError("MONTHLY_ODS_LOCAL_ORIGINAL_MEMBER_CLOSURE_CHANGED")
        for f in original:
            target = (
                f["path"]
                if s["role"] == "historical_seed"
                else (f"payload/increments/{s['role']}/{s['source_id']}/{f['path']}")
            )
            if target in independently_expected:
                raise MonthlyOdsError("MONTHLY_ODS_TARGET_COLLISION")
            independently_expected.add(target)
            actual = files_by_path.get(target, {})
            suffix = Path(f["path"]).suffix
            expected_format = (
                "parquet" if suffix == ".parquet" else "sqlite" if suffix == ".db" else "raw"
            )
            if (
                actual.get("source_id") != s["source_id"]
                or actual.get("original_path") != f["path"]
                or any(actual.get(k) != f[k] for k in ("size_bytes", "sha256"))
                or actual.get("format") != expected_format
            ):
                raise MonthlyOdsError("MONTHLY_ODS_MEMBER_SOURCE_BINDING_CHANGED")
        for name in [s["manifest_name"], "READY"]:
            target = base + name
            if (
                target in independently_expected
                or files_by_path.get(target, {}).get("source_id") != s["source_id"]
            ):
                raise MonthlyOdsError("MONTHLY_ODS_MEMBER_SOURCE_BINDING_CHANGED")
            independently_expected.add(target)
    if independently_expected != expected:
        raise MonthlyOdsError("MONTHLY_ODS_UNDECLARED_ORIGINAL_MEMBER")
    if any(not set(contract["calendar_authority"]["open_dates"]) <= covered[r] for r in covered):
        raise MonthlyOdsError("MONTHLY_ODS_REQUIRED_DAILY_ROLE_MISSING")
    # All cheap policy/manifest/namespace gates precede the expensive full-byte pass.
    census = {}
    for f in plan["files"]:
        p = _member(root, f["path"])
        budget.check()
        if (
            p.stat().st_nlink != 1
            or p.stat().st_size != f["size_bytes"]
            or file_sha(p) != f["sha256"]
        ):
            raise MonthlyOdsError("MONTHLY_ODS_PHYSICAL_BYTES_CHANGED")
        if f["format"] == "parquet":
            meta = _parquet_metadata(p, tuple(f["metadata"]["date_bounds"]))
            if meta != f["metadata"]:
                raise MonthlyOdsError("MONTHLY_ODS_NATIVE_SCHEMA_CHANGED")
            native = _native_parquet_census(p, budget)
            if native["rows"] != meta["row_count"]:
                raise MonthlyOdsError("MONTHLY_ODS_FULL_NATIVE_ROW_CENSUS_CHANGED")
            census[f["path"]] = {**native, "schema_sha256": meta["schema_sha256"]}
        elif f["format"] == "sqlite":
            _sqlite_metadata(p)
    result = {
        "purpose": PURPOSE,
        "plan_identity": plan["identity"],
        "frozen_plan_identity": plan["frozen_plan_identity"],
        "file_count": len(expected),
        "file_bytes": sum(f["size_bytes"] for f in plan["files"]),
        "native_census": census,
        "original_manifest_closure": "PASS",
        "source_roots_dereferenced": False,
        "downstream_qualification_inferred": False,
        "READY": False,
    }
    result["identity"] = digest(result)
    if envelope is not None and envelope["content"].get("scan_identity") != result["identity"]:
        raise MonthlyOdsError("MONTHLY_ODS_SEALED_RECOMPUTED_SUMMARY_CHANGED")
    return result


def seal_monthly(
    root: Path,
    budget: Any,
    *,
    expected_contract_digest: str,
    code_revision: str,
    independent_receipts: list[dict[str, Any]],
    expected_negative_probes: dict[str, str],
) -> dict[str, Any]:
    """No READY until actual offline-copy receipts and restored bytes pass.

    The ordinary ODS envelope remains readable; the successor content contract
    explicitly restricts unavailable uses instead of changing the frozen July gate.
    This module does not write CURRENT or change consumer admission.
    """
    root = physical_path(root)
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise FileExistsError("MONTHLY_ODS_SEAL_ALREADY_EXISTS")
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise MonthlyOdsError("MONTHLY_ODS_FULL_CODE_REVISION_REQUIRED")
    current = audit_monthly(root, budget, expected_contract_digest=expected_contract_digest)
    if len(independent_receipts) != 2 or len({r.get("pid") for r in independent_receipts}) != 2:
        raise MonthlyOdsError("MONTHLY_ODS_TWO_NEW_PROCESS_RECEIPTS_REQUIRED")
    for receipt in independent_receipts:
        if (
            receipt.get("identity") != current["identity"]
            or receipt.get("source_network_denied") is not True
            or receipt.get("physical_copy_distinct_inodes") is not True
            or receipt.get("code_revision") != code_revision
            or receipt.get("source_reads_after_probes") != 0
            or receipt.get("network_calls_after_probes") != 0
            or not isinstance(receipt.get("pid"), int)
        ):
            raise MonthlyOdsError("MONTHLY_ODS_INDEPENDENT_COPY_ACCEPTANCE_CHANGED")
    if expected_negative_probes != {
        "missing": "MONTHLY_ODS_PHYSICAL_FILE_SET_CHANGED",
        "corrupt": "MONTHLY_ODS_PHYSICAL_BYTES_CHANGED",
        "restored_all_original_file_hashes": "PASS",
    }:
        raise MonthlyOdsError("MONTHLY_ODS_NEGATIVE_AND_RESTORATION_PROOFS_REQUIRED")
    plan = json.loads((root / "MATERIALIZATION.json").read_bytes())
    if plan["contract"].get("stage") != "ACTIVE_VERSIONED_CONTRACT":
        raise MonthlyOdsError("MONTHLY_ODS_DRAFT_CONTRACT_CANNOT_SEAL")
    files = []
    for f in plan["files"]:
        item = {k: f[k] for k in ("path", "size_bytes", "sha256", "format", "role", "source_id")}
        item["source_path"] = f["archive_source_path"]
        item["dataset_id"] = f["source_id"] + ":" + f["original_path"]
        item.update(f.get("metadata", {}))
        files.append(item)
    # The frozen plan is a package-local original evidence member as well.
    materialization = root / "MATERIALIZATION.json"
    target = root / "payload/source-plan/MATERIALIZATION.json"
    target.parent.mkdir(parents=True)
    with materialization.open("rb") as src, target.open("xb") as dst:
        for b in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(b)
        dst.flush()
        os.fsync(dst.fileno())
    if file_sha(target) != file_sha(materialization):
        raise MonthlyOdsError("MONTHLY_ODS_LOCAL_PLAN_COPY_CHANGED")
    files.append(
        {
            "path": "payload/source-plan/MATERIALIZATION.json",
            "source_path": "job-local frozen plan",
            "dataset_id": "independent_raw_compaction_plan",
            "role": "local_governance_evidence",
            "format": "json",
            "size_bytes": target.stat().st_size,
            "sha256": file_sha(target),
        }
    )
    content = {
        "contract_id": plan["contract"]["contract_id"],
        "policy_sha256": expected_contract_digest,
        "source_node": "hermes",
        "mirror_nodes": ["hermes", "cn01"],
        "through_date": plan["business_through"],
        "increment_start": "2026-08-01",
        "authority": {
            "execution_authorized": False,
            "historical_source": "hermes",
            "oci_ods_enabled": False,
            "reconstruction_seed": True,
        },
        "governance_purpose": PURPOSE,
        "files": files,
        "source_inventory": plan["sources"],
        "scope_gaps": plan["contract"]["intraday"]["availability_gaps"],
        "time_and_use_contract": plan["contract"],
        "independent_acceptance": independent_receipts,
        "negative_probes": expected_negative_probes,
        "scan_identity": current["identity"],
        "consumer_admission": "NOT_GRANTED",
    }
    manifest = {
        "schema_version": 1,
        "kind": "quanthus_ods_base_bundle",
        "bundle_id": root.name,
        "built_at": plan["build_UTC"],
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": code_revision,
        "content_digest": digest(content),
        "content": content,
        "storage_reuse": {"file_count": 0, "size_bytes": 0},
    }
    # Job plan path moves only after its exact independently copied evidence is durable.
    materialization.unlink()
    if _tree(root) != {f["path"] for f in files}:
        raise MonthlyOdsError("MONTHLY_ODS_FINAL_PHYSICAL_SET_CHANGED")
    with (root / "manifest.json").open("xb") as f:
        f.write(canonical(manifest) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    with (root / "READY").open("x") as f:
        f.write(file_sha(root / "manifest.json") + "\n")
        f.flush()
        os.fsync(f.fileno())
    return manifest
