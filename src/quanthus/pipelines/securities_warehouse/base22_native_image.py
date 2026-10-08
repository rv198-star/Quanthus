"""Byte-preserving assembly and bounded offline audit of the native 33-table image.

Domain adapters own repairs. This assembler consumes their pinned qualified
entities once, copies independent physical files, and records restricted uses.
No historical row JSON database, supplier access or production pointer is used.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base22 import CONTRACT_SHA, EXTENSION_SHA, MODEL_SHA, _physical, digest, file_sha, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, identifier
from .base22_delivery import HARD_CHECKS, PURPOSE, assess_delivery, validate_contract
from .base22_holder_component_union import check_component_global_keys
from .base22_sources import _fingerprint, _member, _tree, disjoint_output, physical_path

KIND = "dwd_base22_native_purpose_qualified_candidate_v2"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _declared_hashes(value: Any) -> dict[str, str]:
    """Read declarations only; an archived source_root is never dereferenced."""
    found: dict[str, str] = {}

    def add(path: str, sha: str) -> None:
        if path in found and found[path] != sha:
            raise BoundedError("SOURCE_DESCRIPTOR_CONFLICTING_MEMBER")
        found[path] = sha

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                add(item["path"], item["sha256"])
            for key, child in item.items():
                if isinstance(key, str) and "/" in key:
                    if isinstance(child, str) and child.startswith("sha256:"):
                        add(key, child)
                    elif isinstance(child, dict) and isinstance(child.get("sha256"), str):
                        add(key, child["sha256"])
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    if isinstance(value, dict) and "physical_members" in value:
        for path, item in value["physical_members"].items():
            add(path, item["sha256"])
    elif isinstance(value, dict) and "physical_files" in value:
        for path, sha in value["physical_files"].items():
            add(path, sha)
    else:
        visit(value.get("files", value.get("outputs", value)) if isinstance(value, dict) else value)
    return found


def validate_selection(
    plan: Mapping[str, Any], rules: Mapping[str, Any], descriptors: Mapping[str, Any]
) -> None:
    """Shared offline structural admission before source opening or output writes."""
    if (
        plan.get("purpose") != PURPOSE
        or plan.get("through_date") != "2026-08-31"
        or plan.get("timezone") != "Asia/Shanghai"
        or set(plan.get("tables", {})) != set(rules["tables"])
        or set(plan.get("sources", {})) != set(descriptors)
    ):
        raise BoundedError("NATIVE_IMAGE_SELECTION_SCOPE_INVALID")
    members = {name: _declared_hashes(body) for name, body in descriptors.items()}
    for name, source in plan["sources"].items():
        if (
            not name.isidentifier()
            or source.get("quality_gate") != "QUALIFIED_FOR_GOVERNANCE_RETENTION"
            or source.get("role") not in {"retained_native", "qualified_domain_output"}
        ):
            raise BoundedError("NATIVE_IMAGE_SOURCE_PURPOSE_OR_QUALIFICATION_INVALID")
    for table, entry in plan["tables"].items():
        ids = entry.get("source_ids", [])
        if not ids or len(ids) != len(set(ids)) or not set(ids) <= set(descriptors):
            raise BoundedError("NATIVE_IMAGE_TABLE_SOURCE_CLOSURE_INVALID")
        files = entry.get("files", [])
        parts: Counter[str] = Counter()
        seen: set[tuple[str, str]] = set()
        if not files:
            raise BoundedError("NATIVE_IMAGE_TABLE_FILES_MISSING")
        for item in files:
            sid, relative = item["source_id"], item["path"]
            if (
                sid not in ids
                or item.get("table") != table
                or members[sid].get(relative) != item.get("sha256")
                or not isinstance(item.get("rows"), int)
                or isinstance(item["rows"], bool)
                or item["rows"] < 0
                or not isinstance(item.get("size_bytes"), int)
                or isinstance(item["size_bytes"], bool)
                or item["size_bytes"] <= 0
                or not isinstance(item.get("partition"), str)
                or not item["partition"]
                or (sid, relative) in seen
            ):
                raise BoundedError("NATIVE_IMAGE_UNDECLARED_OR_CONFLICTING_INPUT")
            _member(Path("/offline-archive"), relative)
            _member(Path("/offline-partition"), item["partition"])
            seen.add((sid, relative))
            parts[item["partition"]] += item["rows"]
        if (
            dict(parts) != entry.get("expected_partition_rows")
            or sum(parts.values()) != entry["rows"]
        ):
            raise BoundedError("NATIVE_IMAGE_FROZEN_PARTITION_CENSUS_CHANGED")
        if (not entry["rows"] and set(parts) != {"empty"}) or (
            entry["rows"] and any(n == 0 for n in parts.values())
        ):
            raise BoundedError("NATIVE_IMAGE_EXTRA_OR_RENAMED_EMPTY_PARTITION")


def _normalized_batches(paths: list[Path], schema: Any, rows: int) -> Iterator[Any]:
    """Fixed-size Arrow batches, independent of source files/row-group boundaries."""
    pa, pc, pq = (
        importlib.import_module("pyarrow" + name) for name in ("", ".compute", ".parquet")
    )
    pending: list[Any] = []
    count = 0
    for path in paths:
        pf = pq.ParquetFile(path)
        if not pf.schema_arrow.equals(schema, check_metadata=False):
            raise BoundedError("NATIVE_IMAGE_PHYSICAL_SCHEMA_CHANGED")
        for batch in pf.iter_batches(batch_size=rows):
            offset = 0
            while offset < batch.num_rows:
                take = min(rows - count, batch.num_rows - offset)
                pending.append(batch.slice(offset, take))
                offset += take
                count += take
                if count == rows:
                    table = pa.Table.from_batches(pending, schema=schema).combine_chunks()
                    # take compacts sliced validity/string buffers to canonical offsets.
                    yield pc.take(table, pa.array(range(count), type=pa.int64())).to_batches()[0]
                    pending, count = [], 0
    if pending:
        table = pa.Table.from_batches(pending, schema=schema).combine_chunks()
        yield pc.take(table, pa.array(range(count), type=pa.int64())).to_batches()[0]


def typed_summary(
    paths: list[Path], schema: Any, *, check: Callable[[], None], batch_rows: int = 8192
) -> dict[str, Any]:
    """Scan every value without converting protected prices to Python documents.

    IPC encoding preserves Decimal scale, timestamps, nulls and zero values. Fixed
    batches and compacted buffers give identical summaries after repartitioning or
    relocation. The Arrow runtime version is recorded in the image receipt.
    """
    h = hashlib.sha256(schema.remove_metadata().serialize().to_pybytes())
    count = 0
    for batch in _normalized_batches(paths, schema.remove_metadata(), batch_rows):
        check()
        data = batch.serialize().to_pybytes()
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
        count += batch.num_rows
    return {
        "rows": count,
        "typed_arrow_sha256": "sha256:" + h.hexdigest(),
        "batch_rows": batch_rows,
    }


def image_schemas(model: dict[str, Any], extension: dict[str, Any]) -> dict[str, Any]:
    pa = importlib.import_module("pyarrow")
    result = schemas(model, extension)
    table = "dwd_shareholder_count_observation"
    result[table] = result[table].append(pa.field("average_holding_exact", pa.decimal256(41, 13)))
    return result


def check_partition_coordinates(
    scan: GlobalScan,
    *,
    paths: list[Path],
    part: str,
    rule: Mapping[str, Any],
    schema: Any,
    rows: int,
) -> None:
    """Vectorized date-only projection, never a full historical Python row loop."""
    pa = importlib.import_module("pyarrow")
    if not rows:
        if part != "empty":
            raise BoundedError("NATIVE_IMAGE_EMPTY_PARTITION_CHANGED")
        return
    if not rule["partition_by"]:
        if part != "all":
            raise BoundedError("NATIVE_IMAGE_FLAT_PARTITION_CHANGED")
        return
    labels = [p.split("=", 1) for p in part.split("/")]
    if any(len(p) != 2 for p in labels) or [p[0] for p in labels] != rule["partition_by"]:
        raise BoundedError("NATIVE_IMAGE_PARTITION_LABEL_CHANGED")
    predicates = []
    for field, value in labels:
        if field.endswith(("_year", "_month")):
            source, unit = field.rsplit("_", 1)
            expr = identifier(source)
            if pa.types.is_timestamp(schema.field(source).type):
                expr = "timezone('Asia/Shanghai'," + expr + ")"
            expr = unit + "(" + expr + ")"
        else:
            expr = identifier(field)
        literal = "'" + value.replace("'", "''") + "'"
        predicates.append(f"CAST({expr} AS VARCHAR) IS DISTINCT FROM {literal}")
    view = "image_partition_coordinates"
    scan.connection.read_parquet([str(p) for p in paths], hive_partitioning=False).create_view(
        view, replace=True
    )
    scan.reject(
        "SELECT 1 FROM " + view + " WHERE " + " OR ".join(predicates) + " LIMIT 1",
        "NATIVE_IMAGE_PARTITION_COORDINATE_CHANGED",
    )
    scan.connection.execute("DROP VIEW " + view)


def _check_directories(root: Path, files: set[str]) -> None:
    expected = {str(p) for relative in files for p in Path(relative).parents if str(p) != "."}
    actual = set()

    def failed(error: OSError) -> None:
        raise BoundedError("NATIVE_IMAGE_DIRECTORY_SCAN_FAILED") from error

    for parent, dirs, _ in os.walk(root, onerror=failed, followlinks=False):
        for directory in dirs:
            p = Path(parent) / directory
            if p.is_symlink():
                raise BoundedError("NATIVE_IMAGE_DIRECTORY_SYMLINK")
            actual.add(str(p.relative_to(root)))
    if actual != expected:
        raise BoundedError("NATIVE_IMAGE_UNDECLARED_PHYSICAL_DIRECTORY")


def materialize_native_image(
    *,
    plan: dict[str, Any],
    rules: dict[str, Any],
    contract: dict[str, Any],
    model: dict[str, Any],
    extension: dict[str, Any],
    limitations: list[dict[str, Any]],
    local_evidence: dict[str, Path],
    output: Path,
    allocated_limit: int,
    free_floor: int,
    rss_limit: int,
    wall_seconds: int,
    config_root: Path,
) -> dict[str, Any]:
    """Copy pinned independent entities; leave a materialization, never write READY.

    Capacity registration is supplied by the task, including retained audit copies.
    Each exclusive copy is bounded by its frozen byte size; no generic cleanup or
    cache is introduced. Partials and the selection survive a failure.
    """
    validate_contract(contract)
    # Freeze caller-owned mappings before any writes.
    plan = json.loads(json.dumps(plan))
    roots = {k: physical_path(Path(v["root"])) for k, v in plan["sources"].items()}
    output = disjoint_output(output, list(roots.values()) + list(local_evidence.values()))
    if output.exists():
        raise BoundedError("NATIVE_IMAGE_OUTPUT_ALREADY_EXISTS")
    descriptors: dict[str, Any] = {}
    descriptor_bytes: dict[str, bytes] = {}
    for sid, binding in plan["sources"].items():
        p = _member(roots[sid], binding["descriptor"])
        if file_sha(p) != binding["descriptor_sha256"]:
            raise BoundedError("NATIVE_IMAGE_SOURCE_DESCRIPTOR_CHANGED")
        descriptor_bytes[sid] = p.read_bytes()
        descriptors[sid] = json.loads(descriptor_bytes[sid])
    validate_selection(plan, rules, descriptors)
    expected_schemas = image_schemas(model, extension)
    configuration = {
        "dwd-domain-model.v1.json": (model, MODEL_SHA),
        "dwd-domain-model.v1.1-extension.json": (extension, EXTENSION_SHA),
        "base22-independent-image.v1.json": (rules, CONTRACT_SHA),
    }
    configuration_bytes = {}
    for name, (value, sha) in configuration.items():
        p = physical_path(config_root / name)
        if file_sha(p) != sha or json.loads(p.read_bytes()) != value:
            raise BoundedError("NATIVE_IMAGE_FROZEN_CONFIGURATION_CHANGED")
        configuration_bytes[name] = p.read_bytes()
    started, allocated = time.monotonic(), 0

    def guard(reserve: int = 0) -> None:
        parent = output if output.exists() else output.parent
        while not parent.exists():
            parent = parent.parent
        fs = os.statvfs(parent)
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        if allocated + reserve > allocated_limit:
            raise BoundedError("NATIVE_IMAGE_REGISTERED_WRITE_LIMIT")
        if fs.f_bavail * fs.f_frsize - reserve < free_floor:
            raise BoundedError("NATIVE_IMAGE_REGISTERED_FREE_FLOOR")
        if rss > rss_limit or time.monotonic() - started > wall_seconds:
            raise BoundedError("NATIVE_IMAGE_REGISTERED_RSS_OR_TIME_LIMIT")

    guard(
        sum(
            math.ceil(i["size_bytes"] / 4096) * 4096
            for v in plan["tables"].values()
            for i in v["files"]
        )
    )
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "evidence/input-plan.json", plan)
    _write(output / "contract/base22-delivery.v2.json", contract)
    for name, data in configuration_bytes.items():
        with (output / "contract" / name).open("xb") as stream:
            stream.write(data)
    _write(output / "evidence/limitations.json", limitations)
    for sid, data in descriptor_bytes.items():
        p = output / "evidence/sources" / (sid + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("xb") as stream:
            stream.write(data)
    for relative, source in local_evidence.items():
        data = physical_path(source).read_bytes()
        p = _member(output, relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("xb") as stream:
            stream.write(data)
    allocated = sum((output / p).stat().st_blocks * 512 for p in _tree(output))
    pq = importlib.import_module("pyarrow.parquet")
    layout: dict[str, Any] = {}
    for table, entry in sorted(plan["tables"].items()):
        files = []
        for n, item in enumerate(entry["files"]):
            source = _member(roots[item["source_id"]], item["path"])
            before = _fingerprint(source)
            if before[2] != item["size_bytes"] or source.stat().st_nlink != 1:
                raise BoundedError("NATIVE_IMAGE_INPUT_PHYSICAL_IDENTITY_CHANGED")
            relative = f"payload/{table}/{item['partition']}/part-{n:06d}.parquet"
            target = _member(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            guard(math.ceil(item["size_bytes"] / 4096) * 4096)
            h, copied = hashlib.sha256(), 0
            with source.open("rb") as src, target.open("xb") as dst:
                while data := src.read(1024 * 1024):
                    if copied + len(data) > item["size_bytes"]:
                        raise BoundedError("NATIVE_IMAGE_INPUT_GREW")
                    dst.write(data)
                    h.update(data)
                    copied += len(data)
                    guard()
            allocated += target.stat().st_blocks * 512
            if (
                "sha256:" + h.hexdigest() != item["sha256"]
                or copied != item["size_bytes"]
                or _fingerprint(source) != before
                or target.stat().st_nlink != 1
            ):
                raise BoundedError("NATIVE_IMAGE_INPUT_CHANGED_DURING_COPY")
            pf = pq.ParquetFile(target)
            if (
                not pf.schema_arrow.equals(expected_schemas[table], check_metadata=False)
                or pf.metadata.num_rows != item["rows"]
            ):
                raise BoundedError("NATIVE_IMAGE_INPUT_SCHEMA_OR_ROWS_CHANGED")
            files.append({**item, "archive_source_path": item["path"], "path": relative})
        layout[table] = {
            "files": files,
            "expected_partition_rows": entry["expected_partition_rows"],
        }
        print(
            json.dumps({"materialized_table": table, "files": len(files), "rows": entry["rows"]}),
            flush=True,
        )
    record = {
        "kind": KIND,
        "purpose": PURPOSE,
        "plan_digest": digest(plan),
        "layout": layout,
        "source_descriptors": {sid: v["descriptor_sha256"] for sid, v in plan["sources"].items()},
        "evidence": {
            p: file_sha(output / p) for p in sorted(_tree(output)) if not p.startswith("payload/")
        },
        "build_UTC": datetime.now(timezone.utc).isoformat(),
        "registered_limits": {
            "allocated_bytes": allocated_limit,
            "free_floor_bytes": free_floor,
            "rss_bytes": rss_limit,
            "wall_seconds": wall_seconds,
        },
        "actual_allocated_bytes": allocated,
        "READY": False,
        "consumer_admission": "NOT_GRANTED",
    }
    _write(output / "MATERIALIZATION.json", record)
    return record


def audit_native_image(root: Path, budget: Budget) -> dict[str, Any]:
    """All runtime evidence is local; source roots are inert archival strings."""
    root = physical_path(root)
    if not os.statvfs(root).f_flag & os.ST_RDONLY:
        raise BoundedError("NATIVE_IMAGE_KERNEL_READONLY_REQUIRED")
    record = json.loads((root / "MATERIALIZATION.json").read_bytes())
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    contract = json.loads((root / "contract/base22-delivery.v2.json").read_bytes())
    rules = json.loads((root / "contract/base22-independent-image.v1.json").read_bytes())
    model = json.loads((root / "contract/dwd-domain-model.v1.json").read_bytes())
    extension = json.loads((root / "contract/dwd-domain-model.v1.1-extension.json").read_bytes())
    limitations = json.loads((root / "evidence/limitations.json").read_bytes())
    validate_contract(contract)
    if (
        record.get("kind") != KIND
        or record.get("purpose") != PURPOSE
        or record["plan_digest"] != digest(plan)
        or file_sha(root / "contract/dwd-domain-model.v1.json") != MODEL_SHA
        or file_sha(root / "contract/dwd-domain-model.v1.1-extension.json") != EXTENSION_SHA
        or file_sha(root / "contract/base22-independent-image.v1.json") != CONTRACT_SHA
    ):
        raise BoundedError("NATIVE_IMAGE_CONTRACT_OR_PLAN_CHANGED")
    descriptors = {}
    for sid in plan["sources"]:
        p = root / "evidence/sources" / (sid + ".json")
        if file_sha(p) != plan["sources"][sid]["descriptor_sha256"]:
            raise BoundedError("NATIVE_IMAGE_LOCAL_SOURCE_EVIDENCE_CHANGED")
        descriptors[sid] = json.loads(p.read_bytes())
    validate_selection(plan, rules, descriptors)
    for relative, sha in record["evidence"].items():
        if file_sha(_physical(root, relative, independent=True)) != sha:
            raise BoundedError("NATIVE_IMAGE_LOCAL_EVIDENCE_CHANGED")
    ss = image_schemas(model, extension)
    if set(record["layout"]) != set(ss):
        raise BoundedError("NATIVE_IMAGE_EXACT_TABLE_SET_CHANGED")
    declared = set(record["evidence"]) | {"MATERIALIZATION.json"}
    if (root / "manifest.json").exists():
        declared |= {"manifest.json", "READY"}
    summaries = {}
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in sorted(ss):
            budget.begin_stage("whole-table-" + table)
            entry = record["layout"][table]
            frozen = plan["tables"][table]
            if len(entry["files"]) != len(frozen["files"]):
                raise BoundedError("NATIVE_IMAGE_FROZEN_FILE_CENSUS_CHANGED")
            parts: dict[str, list[Path]] = {}
            counts: Counter[str] = Counter()
            for n, (item, original) in enumerate(zip(entry["files"], frozen["files"], strict=True)):
                expected = {
                    **original,
                    "archive_source_path": original["path"],
                    "path": f"payload/{table}/{original['partition']}/part-{n:06d}.parquet",
                }
                if item != expected or item["path"] in declared:
                    raise BoundedError("NATIVE_IMAGE_FROZEN_FILE_IDENTITY_CHANGED")
                declared.add(item["path"])
                p = _physical(root, item["path"], independent=True)
                if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
                    raise BoundedError("NATIVE_IMAGE_PHYSICAL_BYTES_CHANGED")
                pq = importlib.import_module("pyarrow.parquet")
                pf = pq.ParquetFile(p)
                if pf.metadata.num_rows != item["rows"]:
                    raise BoundedError("NATIVE_IMAGE_ROW_CENSUS_CHANGED")
                parts.setdefault(item["partition"], []).append(p)
                counts[item["partition"]] += item["rows"]
            if (
                dict(counts) != frozen["expected_partition_rows"]
                or dict(counts) != entry["expected_partition_rows"]
            ):
                raise BoundedError("NATIVE_IMAGE_PHYSICAL_PARTITION_SET_CHANGED")
            paths = [p for part in parts.values() for p in part]
            scan.add(table, paths)
            if table == "dwd_shareholding_observation":
                check_component_global_keys(scan, table)
            else:
                scan.keys(
                    table,
                    rules["tables"][table]["business_key"],
                    rules["tables"][table]["version_key"],
                )
            scan.row_rules(table, ss[table], rules["tables"][table])
            admitted = plan["tables"][table]["admitted_lineage"]
            by_source = frozen["lineage_by_source"]
            if (
                set(by_source) != set(frozen["source_ids"])
                or sorted({tuple(v) for vals in by_source.values() for v in vals})
                != [tuple(v) for v in admitted]
                or not admitted
                or any(
                    len(v) != 3 or any(not isinstance(x, str) or not x for x in v) for v in admitted
                )
            ):
                raise BoundedError("NATIVE_IMAGE_OFFLINE_TABLE_LINEAGE_REGISTRY_INVALID")
            scan.lineage(table, [tuple(v) for v in admitted])
            for part, files in parts.items():
                check_partition_coordinates(
                    scan,
                    paths=files,
                    part=part,
                    rule=rules["tables"][table],
                    schema=ss[table],
                    rows=counts[part],
                )
            summaries[table] = {
                part: typed_summary(files, ss[table], check=budget.check)
                for part, files in parts.items()
            }
            print(json.dumps({"audited_table": table, "rows": sum(counts.values())}), flush=True)
        budget.begin_stage("whole-image-references")
        scan.references(model, rules)
    if _tree(root) != declared:
        raise BoundedError("NATIVE_IMAGE_UNDECLARED_PHYSICAL_FILE")
    _check_directories(root, declared)
    checks = dict.fromkeys(HARD_CHECKS, "PASS")
    checks["two_independent_source_denied_cold_runs"] = "NOT_RUN"
    checks["missing_and_corrupt_fail_closed"] = "NOT_RUN"
    assessment = assess_delivery(
        contract=contract,
        tables=set(ss),
        limitations=limitations,
        evidence=record["evidence"],
        checks=checks,
    )
    result = {
        "kind": KIND,
        "materialization_sha256": file_sha(root / "MATERIALIZATION.json"),
        "input_plan_digest": record["plan_digest"],
        "local_evidence_digests": record["evidence"],
        "summaries": summaries,
        "assessment": assessment,
        "pyarrow_version": importlib.import_module("pyarrow").__version__,
        "global_keys_versions_references_lineage": "PASS",
    }
    result["identity"] = digest(result)
    if (root / "manifest.json").exists():
        manifest = json.loads((root / "manifest.json").read_bytes())
        if (
            manifest.get("kind") != KIND
            or manifest.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
            or manifest.get("scan_identity") != result["identity"]
            or manifest.get("content_digest")
            != digest({k: v for k, v in manifest.items() if k != "content_digest"})
            or (root / "READY").read_text().strip() != file_sha(root / "manifest.json")
        ):
            raise BoundedError("NATIVE_IMAGE_READY_OR_RECOMPUTED_SUMMARIES_CHANGED")
    return result


def seal_native_image(
    root: Path, receipt: Mapping[str, Any], *, code_revision: str
) -> dict[str, Any]:
    """Seal only a successful full structural scan; independent acceptance follows.

    Called on the mutable job-owned root outside its readonly audit namespace.
    No sealed output can be overwritten. READY is always the last image file.
    """
    if (root / "READY").exists() or (root / "manifest.json").exists():
        raise BoundedError("NATIVE_IMAGE_SEALED_OUTPUT_ALREADY_EXISTS")
    if (
        not receipt.get("assessment", {}).get("build_eligible")
        or receipt.get("kind") != KIND
        or receipt.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
        or receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
    ):
        raise BoundedError("NATIVE_IMAGE_FULL_STRUCTURAL_SCAN_REQUIRED")
    manifest = {
        "kind": KIND,
        "purpose": PURPOSE,
        "version": "2.2",
        "business_through": "2026-08-31",
        "business_timezone": "Asia/Shanghai",
        "materialization_sha256": file_sha(root / "MATERIALIZATION.json"),
        "scan_identity": receipt["identity"],
        "code_revision": code_revision,
        "assessment": receipt["assessment"],
        "summaries": receipt["summaries"],
        "runtime": {"pyarrow": receipt["pyarrow_version"]},
        "independent_acceptance": "NOT_RUN_SEPARATE_RECEIPTS_REQUIRED",
        "consumer_admission": "NOT_GRANTED",
    }
    manifest["content_digest"] = digest(manifest)
    _write(root / "manifest.json", manifest)
    with (root / "READY").open("x", encoding="utf-8") as stream:
        stream.write(file_sha(root / "manifest.json") + "\n")
    return manifest
