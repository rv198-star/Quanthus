"""Bounded whole action-domain materialization of qualified exact unit revisions.

The immutable local prerequisite is consumed once per construction/audit mode.
This is an action-domain output; whole Base 2.3 acceptance remains separate.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition
from .base22_bounded import BoundedError, Budget, Member, semantic_scan
from .base22_sources import _tree
from .base23_action_scope import copy_member
from .base23_merge import rewrite_knowledge_file
from .base23_native_image import image_schemas
from .base23_required import CONTRACT_ID
from .base23_required_scope import audit_required_scope


def materialize_required_union(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    local = root / "evidence/required"
    receipt = audit_required_scope(local, budget)
    dependency = json.loads((local / "SCOPE.json").read_bytes())
    contract, model, extension = load_contract(local / "evidence/contract")
    ss = image_schemas(model, extension)
    result: dict[str, Any] = {
        "contract_id": CONTRACT_ID,
        "kind": "WHOLE_ACTION_DOMAIN_UNIT_SUCCESSOR",
        "dependency_sha256": file_sha(local / "SCOPE.json"),
        "dependency_replay_identity": receipt["identity"],
        "outputs": {},
        "closures": {},
        "semantic_scans": {},
        "complete_Base23": False,
        "consumer_admission": "NOT_GRANTED",
    }
    for table, items in dependency["outputs"].items():
        output = [
            row for item in items for row in pq.ParquetFile(local / item["path"]).read().to_pylist()
        ]
        rule = contract["tables"][table]
        old = {
            tuple(r[f] for f in rule["version_key"]): r
            for r in output
            if r["knowledge_to"] is not None
        }
        added = [r for r in output if r["knowledge_to"] is None]
        if len(old) * 2 != len(output) or len(old) != len(added):
            raise BoundedError("REQUIRED_UNION_COMPLETE_OLD_NEW_VERSION_PAIRS_INVALID")
        seen = set()
        sequence: dict[str, int] = defaultdict(int)
        files, closures = [], []
        for item in dependency["inputs"]["native"][table]:
            src = local / item["path"]
            changes = []
            ordinal = 0
            for batch in pq.ParquetFile(src).iter_batches(batch_size=budget.limits.batch_rows):
                for row in batch.to_pylist():
                    key = tuple(row[f] for f in rule["version_key"])
                    if key in old:
                        if (
                            key in seen
                            or row["knowledge_to"] is not None
                            or (
                                digest({**row, "knowledge_to": old[key]["knowledge_to"]})
                                != digest(old[key])
                            )
                        ):
                            raise BoundedError(
                                "REQUIRED_UNION_ONLY_EXACT_OPEN_CLOCK_CLOSURE_ALLOWED"
                            )
                        changes.append((ordinal, old[key]["knowledge_to"]))
                        seen.add(key)
                    ordinal += 1
                budget.check(extra_rss=batch.nbytes * 2)
            part = item["partition"]
            rel = f"payload/{table}/{part}/part-{sequence[part]:06d}.parquet"
            sequence[part] += 1
            dst = root / rel
            if changes:
                member = Member(src, item["sha256"], item["size_bytes"], item["rows"])
                proof = rewrite_knowledge_file(
                    member, dst, schema=ss[table], closures=changes, budget=budget
                )
                closures.append({"source_path": item["path"], "output_path": rel, "proof": proof})
            else:
                copy_member(src, dst, item, budget)
            files.append(
                {
                    **item,
                    "path": rel,
                    "sha256": file_sha(dst),
                    "size_bytes": dst.stat().st_size,
                    "origin": "native",
                }
            )
        if seen != set(old):
            raise BoundedError("REQUIRED_UNION_OLD_VERSION_NOT_FOUND")
        parts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in added:
            parts[partition(row, rule)].append(row)
        for part, rows in sorted(parts.items()):
            rel = f"payload/{table}/{part}/part-{sequence[part]:06d}.parquet"
            dst = budget.destination(root / rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            typed = pa.Table.from_pylist(rows, schema=ss[table])
            with dst.open("xb") as stream:
                pq.write_table(typed, stream, compression="zstd")
            if pq.ParquetFile(dst).read() != typed:
                raise BoundedError("REQUIRED_UNION_NEW_TYPED_ATOM_SERIALIZATION_CHANGED")
            files.append(
                {
                    "path": rel,
                    "partition": part,
                    "sha256": file_sha(dst),
                    "size_bytes": dst.stat().st_size,
                    "rows": len(rows),
                    "origin": "successor",
                }
            )
        result["outputs"][table] = files
        result["closures"][table] = closures
        result["semantic_scans"][table] = semantic_scan(
            [root / i["path"] for i in files], ss[table], check=budget.check
        )
    result["code_sha256"] = file_sha(Path(__file__))
    result["merge_code_sha256"] = file_sha(Path(__file__).with_name("base23_merge.py"))
    return result


def audit_required_union(
    root: Path, budget: Budget, *, require_ready: bool = True
) -> dict[str, Any]:
    import pyarrow.parquet as pq

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("complete_Base23") is not False
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("code_sha256") != file_sha(Path(__file__))
        or body.get("merge_code_sha256") != file_sha(Path(__file__).with_name("base23_merge.py"))
    ):
        raise BoundedError("REQUIRED_UNION_IDENTITY_OR_CODE_CHANGED")
    expected = {"SCOPE.json", *body["physical_members"]}
    if require_ready:
        expected.add("READY")
        if (root / "READY").read_text().strip() != file_sha(root / "SCOPE.json"):
            raise BoundedError("REQUIRED_UNION_READY_CHANGED")
    if _tree(root) != expected:
        raise BoundedError("REQUIRED_UNION_PHYSICAL_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
            raise BoundedError("REQUIRED_UNION_PHYSICAL_HASH_CHANGED")
        budget.check()
    local = root / "evidence/required"
    receipt = audit_required_scope(local, budget)
    if (
        file_sha(local / "SCOPE.json") != body["dependency_sha256"]
        or receipt["identity"] != body["dependency_replay_identity"]
    ):
        raise BoundedError("REQUIRED_UNION_DEPENDENCY_REPLAY_CHANGED")
    dep = json.loads((local / "SCOPE.json").read_bytes())
    contract, model, extension = load_contract(local / "evidence/contract")
    ss = image_schemas(model, extension)
    if set(body["outputs"]) != set(dep["outputs"]):
        raise BoundedError("REQUIRED_UNION_TABLE_SET_CHANGED")
    from .base23_action_scope import check_output_geometry

    check_output_geometry(root, body, expected)
    wanted_files = {
        i["path"].removeprefix("payload/") for files in body["outputs"].values() for i in files
    }
    if _tree(root / "payload") != wanted_files:
        raise BoundedError("REQUIRED_UNION_OUTPUT_PHYSICAL_SET_CHANGED")
    term_parents = set()
    for table, items in dep["outputs"].items():
        rule = contract["tables"][table]
        repairs = [r for i in items for r in pq.ParquetFile(local / i["path"]).read().to_pylist()]
        updates = {
            tuple(r[f] for f in rule["version_key"]): r
            for r in repairs
            if r["knowledge_to"] is not None
        }
        expected_rows = {}
        for i in dep["inputs"]["native"][table]:
            for batch in pq.ParquetFile(local / i["path"]).iter_batches(
                batch_size=budget.limits.batch_rows
            ):
                for r in batch.to_pylist():
                    key = tuple(r[f] for f in rule["version_key"])
                    if key in expected_rows:
                        raise BoundedError("REQUIRED_UNION_NATIVE_VERSION_DUPLICATE")
                    expected_rows[key] = digest(updates.get(key, r))
                budget.check(extra_rss=batch.nbytes * 2)
        for r in repairs:
            if r["knowledge_to"] is None:
                key = tuple(r[f] for f in rule["version_key"])
                if key in expected_rows:
                    raise BoundedError("REQUIRED_UNION_SUCCESSOR_VERSION_DUPLICATE")
                expected_rows[key] = digest(r)
        for i in body["outputs"][table]:
            p = root / i["path"]
            Member(p, i["sha256"], i["size_bytes"], i["rows"]).verify()
            for batch in pq.ParquetFile(p).iter_batches(batch_size=budget.limits.batch_rows):
                for r in batch.to_pylist():
                    key = tuple(r[f] for f in rule["version_key"])
                    if (
                        expected_rows.pop(key, None) != digest(r)
                        or partition(r, rule) != i["partition"]
                    ):
                        raise BoundedError(
                            "REQUIRED_UNION_EXACT_ALLOWED_DIFFERENCE_OR_PARTITION_FAILED"
                        )
                    # The locked model references corporate_action_id. Older
                    # retained term/action knowledge boundaries need not equal;
                    # exact allowed-change replay above preserves those atoms.
                    # New revisions' complete clocks are separately projected
                    # and verified by the required dependency, never inferred.
                    parent = r["corporate_action_id"]
                    if table == "dwd_corporate_action":
                        term_parents.add(parent)
                    elif parent not in term_parents:
                        raise BoundedError("REQUIRED_UNION_TERM_VERSION_PARENT_MISSING")
                budget.check(extra_rss=batch.nbytes * 2)
        if expected_rows:
            raise BoundedError("REQUIRED_UNION_EXPECTED_VERSION_MISSING")
        if (
            semantic_scan(
                [root / i["path"] for i in body["outputs"][table]], ss[table], check=budget.check
            )
            != body["semantic_scans"][table]
        ):
            raise BoundedError("REQUIRED_UNION_FULL_SEMANTIC_SCAN_CHANGED")
    result = {
        "contract_id": CONTRACT_ID,
        "scope_identity": body["identity"],
        "dependency_replay": receipt["identity"],
        "rows": {t: v["rows"] for t, v in body["semantic_scans"].items()},
        "consumer_admission": "NOT_GRANTED",
        "complete_Base23": False,
    }
    result["identity"] = digest(result)
    return result
