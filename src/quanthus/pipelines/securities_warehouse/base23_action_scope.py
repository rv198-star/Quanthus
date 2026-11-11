"""Physical two-table action union with local replay evidence and no old fallback.

This is a qualified domain intermediate, not a complete Base or consumer grant.
Historical Parquet is copied independently; only exact admitted new observations
are added. The archived native rows and qualified supplement remain local evidence
so cold verification repeats admission instead of trusting a plan hash.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, semantic_scan
from .base22_sources import _tree
from .base23_action_merge import CONTRACT_ID, classify_actions, term_closures
from .base23_dividend import TABLES
from .base23_low_cost_audit import audit_low_cost_scope
from .base23_merge import rewrite_knowledge_file
from .base23_native_image import _check_directories, image_schemas


def check_output_geometry(root: Path, body: dict[str, Any], expected: set[str]) -> None:
    """Check actual table/partition directories, including unexpected empty ones."""
    if set(body["outputs"]) != set(TABLES):
        raise BoundedError("ACTION_SCOPE_TABLE_SET_CHANGED")
    for table, files in body["outputs"].items():
        for item in files:
            prefix = f"payload/{table}/{item['partition']}/"
            path = item["path"]
            if (not path.startswith(prefix)
                    or not re.fullmatch(r"part-\d{6}\.parquet", path.removeprefix(prefix))):
                raise BoundedError("ACTION_SCOPE_PHYSICAL_PARTITION_COORDINATE_CHANGED")
    _check_directories(root, expected)


def metadata_rows(root: Path, files: list[dict[str, Any]], *, actions: bool,
                  budget: Budget) -> Iterator[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    columns = ["corporate_action_id", "effective_at", "knowledge_from", "knowledge_to",
               "record_source"]
    if actions:
        columns += ["security_id", "action_type"]
    for item in files:
        path = _physical(root, item["path"], independent=True)
        Member(path, item["sha256"], item["size_bytes"], item["rows"]).verify()
        pf = pq.ParquetFile(path)
        if pf.metadata.num_rows != item["rows"]:
            raise BoundedError("ACTION_SCOPE_INPUT_ROW_CENSUS_CHANGED")
        ordinal = 0
        for batch in pf.iter_batches(batch_size=budget.limits.batch_rows, columns=columns):
            budget.check(extra_rss=batch.nbytes*2)
            for row in batch.to_pylist():
                yield {**row, "path": item["path"], "ordinal": ordinal}
                ordinal += 1


def replay_admission(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    supplement = root / "evidence/supplement"
    audit_low_cost_scope(supplement, budget)
    incoming = json.loads((supplement / "SCOPE.json").read_bytes())["outputs"]
    old = body["native_inputs"]
    admission = classify_actions(
        metadata_rows(root / "evidence/native", old[TABLES[0]], actions=True, budget=budget),
        metadata_rows(supplement, incoming[TABLES[0]], actions=True, budget=budget),
    )
    admission["term_closures"] = term_closures(
        metadata_rows(root / "evidence/native", old[TABLES[1]], actions=False, budget=budget),
        admission,
    )
    admission["identity"] = digest({k: v for k, v in admission.items() if k != "identity"})
    return admission


def copy_member(source: Path, destination: Path, item: dict[str, Any], budget: Budget) -> None:
    if source.is_symlink() or file_sha(source) != item["sha256"]:
        raise BoundedError("ACTION_SCOPE_INPUT_HASH_CHANGED")
    destination = budget.destination(destination)
    budget.check(reserve=item["size_bytes"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024*1024)
    if (destination.stat().st_nlink != 1
            or (destination.stat().st_dev, destination.stat().st_ino)
            == (source.stat().st_dev, source.stat().st_ino)
            or destination.stat().st_size != item["size_bytes"]
            or file_sha(destination) != item["sha256"] or file_sha(source) != item["sha256"]):
        raise BoundedError("ACTION_SCOPE_COPY_OR_INPUT_CHANGED")


def materialize_union(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Inputs are already isolated local physical evidence owned by this job."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    admission = replay_admission(root, body, budget)
    contract, model, extension = load_contract(root / "evidence/supplement/retained/contract")
    ss = image_schemas(model, extension)
    supplement = root / "evidence/supplement"
    incoming = json.loads((supplement / "SCOPE.json").read_bytes())["outputs"]
    accepted = {(r["corporate_action_id"], r["effective_at"]) for r in admission["accepted"]}
    outputs: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    for table in TABLES:
        changes: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        for item in admission["closures" if table == TABLES[0] else "term_closures"]:
            changes[item["path"]].append((item["ordinal"], item["successor"]))
        sequence: dict[str, int] = defaultdict(int)
        for item in body["native_inputs"][table]:
            src = root / "evidence/native" / item["path"]
            part = item["partition"]
            rel = f"payload/{table}/{part}/part-{sequence[part]:06d}.parquet"
            sequence[part] += 1
            if changes[item["path"]]:
                member = Member(src, item["sha256"], item["size_bytes"], item["rows"])
                rewrite_knowledge_file(member, root/rel, schema=ss[table],
                                       closures=sorted(changes[item["path"]]), budget=budget)
            else:
                copy_member(src, root/rel, item, budget)
            outputs[table].append({**item, "path": rel,
                                   "sha256": file_sha(root/rel),
                                   "size_bytes": (root/rel).stat().st_size,
                                   "origin": "native", "original_path": item["path"]})
        for item in incoming[table]:
            pf = pq.ParquetFile(supplement/item["path"])
            selected = []
            for batch in pf.iter_batches(batch_size=budget.limits.batch_rows):
                mask = pa.array([(k, t) in accepted for k, t in zip(
                    batch.column("corporate_action_id").to_pylist(),
                    batch.column("effective_at").to_pylist(), strict=True)])
                filtered = batch.filter(mask)
                if filtered.num_rows:
                    selected.append(filtered)
                budget.check(extra_rss=batch.nbytes*2)
            if not selected:
                continue
            part = item["partition"]
            rel = f"payload/{table}/{part}/part-{sequence[part]:06d}.parquet"
            sequence[part] += 1
            dst = budget.destination(root/rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            rows = pa.Table.from_batches(selected, schema=ss[table])
            budget.check(reserve=rows.nbytes*2)
            # No Decimal conversion or reconstruction: preserve every typed atom.
            with dst.open("xb") as stream:
                pq.write_table(rows, stream, compression="zstd")
            if pq.ParquetFile(dst).read() != rows:
                raise BoundedError("ACTION_SCOPE_SERIALIZATION_CHANGED_TYPED_ATOMS")
            outputs[table].append({"path": rel, "partition": part, "rows": rows.num_rows,
                                   "sha256": file_sha(dst), "size_bytes": dst.stat().st_size,
                                   "origin": "supplement", "original_path": item["path"]})
        paths = [root/i["path"] for i in outputs[table]]
        body.setdefault("semantic_scans", {})[table] = semantic_scan(paths, ss[table],
                                                                    check=budget.check)
    body.update(outputs=outputs, admission=admission)
    return body


def audit_action_scope(root: Path, budget: Budget, *, require_ready: bool = True) -> dict[str, Any]:
    import pyarrow.parquet as pq

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    if (body.get("contract_id") != CONTRACT_ID or body.get("consumer_admission") != "NOT_GRANTED"
            or body.get("complete_Base23") is not False
            or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
            or body.get("code_sha256") != file_sha(Path(__file__))
            or body.get("admission_code_sha256") != file_sha(Path(__file__).with_name(
                "base23_action_merge.py"))
            or body.get("merge_dependency_sha256") != file_sha(Path(__file__).with_name(
                "base23_merge.py"))
            or body.get("native_schema_dependency_sha256") != file_sha(Path(__file__).with_name(
                "base23_native_image.py"))):
        raise BoundedError("ACTION_SCOPE_CONTRACT_CHANGED")
    expected = {"SCOPE.json", *body["physical_members"]}
    if require_ready:
        expected.add("READY")
        if (root/"READY").read_text().strip() != file_sha(root/"SCOPE.json"):
            raise BoundedError("ACTION_SCOPE_READY_CHANGED")
    if _tree(root) != expected:
        raise BoundedError("ACTION_SCOPE_PHYSICAL_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("ACTION_SCOPE_PHYSICAL_HASH_CHANGED")
    check_output_geometry(root, body, expected)
    manifest_path = root / "evidence/native-manifest.json"
    materialization_path = root / "evidence/native-MATERIALIZATION.json"
    closure = body["input_closure"]
    manifest = json.loads(manifest_path.read_bytes())
    native = json.loads(materialization_path.read_bytes())
    if (file_sha(manifest_path) != closure["native_manifest_sha256"]
            or file_sha(materialization_path) != closure["native_materialization_sha256"]
            or manifest["materialization_sha256"] != closure["native_materialization_sha256"]
            or manifest["purpose"] != "p10_governance_baseline_with_disclosed_coverage"
            or file_sha(root/"evidence/supplement/SCOPE.json") != closure["supplement_sha256"]
            or closure.get("purpose") != CONTRACT_ID):
        raise BoundedError("ACTION_SCOPE_INPUT_CLOSURE_CHANGED")
    for table, files in {**body["native_inputs"], **body["parents"]}.items():
        if files != native["layout"][table]["files"]:
            raise BoundedError("ACTION_SCOPE_NATIVE_DECLARED_FILE_SET_CHANGED")
    replay = replay_admission(root, body, budget)
    if digest(replay) != digest(body["admission"]):
        raise BoundedError("ACTION_SCOPE_ADMISSION_REPLAY_CHANGED")
    contract, model, extension = load_contract(root/"evidence/supplement/retained/contract")
    ss = image_schemas(model, extension)
    if set(body["outputs"]) != set(TABLES):
        raise BoundedError("ACTION_SCOPE_TABLE_SET_CHANGED")
    wanted = {i["path"].removeprefix("payload/") for t in TABLES for i in body["outputs"][t]}
    if _tree(root/"payload") != wanted:
        raise BoundedError("ACTION_SCOPE_PARTITION_SET_CHANGED")
    accepted = {(r["corporate_action_id"], r["effective_at"]) for r in replay["accepted"]}
    reader_roots = [root]
    with GlobalScan(budget, read_only_roots=reader_roots) as scan:
        for table in TABLES:
            paths = [root/i["path"] for i in body["outputs"][table]]
            if semantic_scan(paths, ss[table], check=budget.check) != body["semantic_scans"][table]:
                raise BoundedError("ACTION_SCOPE_SEMANTIC_SCAN_CHANGED")
            old_files = {i["path"]: i for i in body["native_inputs"][table]}
            descriptor = json.loads((root/"evidence/supplement/SCOPE.json").read_bytes())
            incoming = descriptor["outputs"][table]
            new_files = {i["path"]: i for i in incoming}
            closures: dict[str, dict[int, Any]] = defaultdict(dict)
            for c in replay["closures" if table == TABLES[0] else "term_closures"]:
                closures[c["path"]][c["ordinal"]] = c["successor"]
            seen_old = set()
            seen_new = set()
            for item in body["outputs"][table]:
                original = item["original_path"]
                actual = pq.ParquetFile(root/item["path"]).read()
                if item["origin"] == "native":
                    if original not in old_files or original in seen_old:
                        raise BoundedError("ACTION_SCOPE_NATIVE_CENSUS_CHANGED")
                    seen_old.add(original)
                    prior = pq.ParquetFile(root/"evidence/native"/original).read()
                    expected_rows = prior.to_pylist()
                    for ordinal, successor in closures[original].items():
                        expected_rows[ordinal]["knowledge_to"] = successor
                    import pyarrow as pa
                    expected_table = pa.Table.from_pylist(expected_rows, schema=ss[table])
                elif item["origin"] == "supplement":
                    if original not in new_files or original in seen_new:
                        raise BoundedError("ACTION_SCOPE_INCOMING_CENSUS_CHANGED")
                    seen_new.add(original)
                    prior = pq.ParquetFile(root/"evidence/supplement"/original).read()
                    expected_rows = [r for r in prior.to_pylist()
                                     if (r["corporate_action_id"], r["effective_at"]) in accepted]
                    import pyarrow as pa
                    expected_table = pa.Table.from_pylist(expected_rows, schema=ss[table])
                else:
                    raise BoundedError("ACTION_SCOPE_UNKNOWN_ORIGIN")
                original_item = old_files.get(original, new_files.get(original, {}))
                if (item["partition"] != original_item.get("partition")
                        or actual != expected_table or actual.num_rows != item["rows"]):
                    raise BoundedError("ACTION_SCOPE_ALLOWED_TYPED_DIFFERENCE_CHANGED")
            expected_new = {p for p in new_files if any(
                (r["corporate_action_id"], r["effective_at"]) in accepted
                for r in pq.ParquetFile(root/"evidence/supplement"/p).read(
                    columns=["corporate_action_id", "effective_at"]).to_pylist())}
            if seen_old != set(old_files) or seen_new != expected_new:
                raise BoundedError("ACTION_SCOPE_EXACT_INPUT_UNION_CHANGED")
            scan.add(table, paths)
            rule = contract["tables"][table]
            scan.keys(table, rule["business_key"], rule["version_key"])
        for table, files in body["parents"].items():
            scan.add(table, [root/"evidence/native"/i["path"] for i in files])
        subset = {*TABLES, *body["parents"]}
        scan.references({"tables": [t for t in model["tables"] if t["name"] in subset]},
                        {"tables": {t: contract["tables"][t] for t in subset}})
    result = {"status": "NATIVE_ACTION_DOMAIN_REPLAY_PASS", "scope_identity": body["identity"],
              "accepted_new_actions": len(replay["accepted"]), "held_actions": len(replay["held"]),
              "rows": {t: body["semantic_scans"][t]["rows"] for t in TABLES},
              "complete_Base23": False, "consumer_admission": "NOT_GRANTED"}
    result["identity"] = digest(result)
    return result
