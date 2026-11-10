"""Exact allowed patch merge and whole-image September successor acceptance.

The existing 33-table native evaluator is unchanged. Scope replay proves actual
patch selection and before/after atoms; the native scan proves the full physical
image, global versions, references, original times and source closure.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha, partition
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_holder_components_v2 import COMPONENT_KEYS
from .base22_native_coordinates import check_decoded
from .base22_sources import _member, _tree
from .base231_domain_pack import schemas
from .base231_native_delta import HOLDING
from .base231_source_qualification import DWD

PREFIX = "evidence/base231-patch"
KIND = "p10_base231_qualified_patch_scope_v1"
TABLES = ("dwd_financial_report", "dwd_financial_concept", "dwd_financial_fact", HOLDING)


def merge_holder_batches(scan: GlobalScan, budget: Budget) -> Iterator[Any]:
    """Change only precisely reproduced old interval ends; append qualified versions."""
    keys = COMPONENT_KEYS + ["knowledge_from"]
    join = " AND ".join("b." + k + " IS NOT DISTINCT FROM c." + k for k in keys)
    fields = schemas()[HOLDING].names
    unchanged = " AND ".join(
        "b." + k + " IS NOT DISTINCT FROM c." + k for k in fields if k != "knowledge_to"
    )
    count = scan.connection.execute("SELECT count(*) FROM closed").fetchone()
    unique = scan.connection.execute(
        "SELECT count(*) FROM (SELECT " + ",".join(keys) + " FROM closed GROUP BY ALL)"
    ).fetchone()
    matched = scan.connection.execute(
        "SELECT count(*) FROM before b JOIN closed c ON "
        + join
        + " WHERE "
        + unchanged
        + " AND b.knowledge_to IS NULL AND "
        "c.knowledge_to>b.knowledge_from"
    ).fetchone()
    if count is None or count != unique or count != matched:
        raise BoundedError("PATCH_UNAUTHORIZED_OLD_INTERVAL_OR_VALUE_CHANGE")
    # Pair every old interval end with exactly one new knowledge version at that
    # end. Representation keys may differ; no old value/exit is fabricated.
    coarse = COMPONENT_KEYS[:-2]
    pair = " AND ".join("c." + k + " IS NOT DISTINCT FROM n." + k for k in coarse)
    pair += " AND c.knowledge_to=n.knowledge_from"
    pairs = scan.connection.execute(
        "SELECT count(*) FROM closed c JOIN revised n ON " + pair
    ).fetchone()
    new_count = scan.connection.execute("SELECT count(*) FROM revised").fetchone()
    if pairs != count or new_count != count:
        raise BoundedError("PATCH_KNOWLEDGE_SUCCESSOR_PAIR_CHANGED")
    selected = ",".join(
        "CASE WHEN c.knowledge_to IS NOT NULL THEN c.knowledge_to "
        "ELSE b.knowledge_to END AS knowledge_to"
        if k == "knowledge_to"
        else "b." + k
        for k in fields
    )
    order = ",".join(COMPONENT_KEYS + ["knowledge_from"])
    reader = scan.connection.execute(
        "SELECT * FROM (SELECT "
        + selected
        + " FROM before b LEFT JOIN closed c ON "
        + join
        + " UNION ALL SELECT * FROM revised UNION ALL SELECT * FROM added) ORDER BY "
        + order
    ).to_arrow_reader(16384)
    for batch in reader:
        budget.check(reserve=batch.nbytes * 2)
        yield batch


def partition_files(
    batches: Any,
    *,
    table: str,
    rule: dict[str, Any],
    schema: Any,
    budget: Budget,
) -> list[dict[str, Any]]:
    """One stream, original arrays preserved, one writer per actual business partition."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    columns = list(
        dict.fromkeys(
            x.rsplit("_", 1)[0] if x.endswith(("_year", "_month")) else x
            for x in rule["partition_by"]
        )
    )
    writers: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    targets: dict[str, Path] = {}
    try:
        for batch in batches:
            budget.check(reserve=batch.nbytes * 2)
            typed = pa.Table.from_batches([batch]).cast(schema, safe=True)
            coordinates = typed.select(columns).to_pylist() if columns else [{}] * len(typed)
            groups: dict[str, list[int]] = {}
            for n, values in enumerate(coordinates):
                groups.setdefault(partition(values, rule), []).append(n)
            for part, indices in sorted(groups.items()):
                selected = typed.take(pa.array(indices, type=pa.int64()))
                check_decoded(selected.to_batches()[0], part, rule)
                if part not in writers:
                    target = budget.root / "outputs" / table / part / "part-000000.parquet"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    writers[part] = pq.ParquetWriter(target, schema, compression="zstd")
                    targets[part] = target
                writers[part].write_table(selected, row_group_size=16384)
                counts[part] += len(selected)
    finally:
        for writer in writers.values():
            writer.close()
    result = []
    for part, path in sorted(targets.items()):
        if pq.ParquetFile(path).metadata.num_rows != counts[part]:
            raise BoundedError("PATCH_SERIALIZED_ROW_CENSUS_CHANGED")
        result.append(
            dict(
                table=table,
                partition=part,
                path=str(path.relative_to(budget.root)),
                rows=counts[part],
                size_bytes=path.stat().st_size,
                sha256=file_sha(path),
            )
        )
    return result


def project_scope(
    *,
    before: Path,
    additive: Path,
    representation: Path,
    budget: Budget,
    read_only_roots: list[Path],
    rules: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    import pyarrow.parquet as pq

    baseline = json.loads((before / "MATERIALIZATION.json").read_bytes())
    outputs: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES[:3]:
        pf = pq.ParquetFile(additive / (table + ".parquet"))
        outputs[table] = partition_files(
            pf.iter_batches(batch_size=16384),
            table=table,
            rule=rules["tables"][table],
            schema=schemas()[table],
            budget=budget,
        )
    with GlobalScan(budget, read_only_roots=read_only_roots) as scan:
        scan.add("before", [before / x["path"] for x in baseline["layout"][HOLDING]["files"]])
        scan.add("closed", [representation / "representation_closed_old.parquet"])
        scan.add("revised", [representation / "representation_revisions.parquet"])
        scan.add("added", [additive / (HOLDING + ".parquet")])
        outputs[HOLDING] = partition_files(
            merge_holder_batches(scan, budget),
            table=HOLDING,
            rule=rules["tables"][HOLDING],
            schema=schemas()[HOLDING],
            budget=budget,
        )
    return outputs


def write_scope(root: Path, outputs: dict[str, Any], *, code_revision: str) -> dict[str, Any]:
    before = root / "inputs/before"
    if file_sha(before / "manifest.json") != DWD:
        raise BoundedError("PATCH_EXACT_BASE_IDENTITY_CHANGED")
    members = {
        p: dict(size_bytes=(root / p).stat().st_size, sha256=file_sha(root / p))
        for p in sorted(_tree(root))
        if not p.startswith("temp/")
    }
    body = dict(
        kind=KIND,
        version="2.3.1",
        business_through="2026-09-30",
        base_manifest_sha256=DWD,
        outputs=outputs,
        physical_members=members,
        code_revision=code_revision,
        evaluator_sha256=file_sha(Path(__file__)),
        consumer_admission="NOT_GRANTED",
        full_image_acceptance=False,
        old_value_changes=0,
        source_change_atoms_merged=0,
    )
    body["identity"] = digest(body)
    (root / "SCOPE.json").open("xb").write(canonical(body))
    # No READY: the patch dependency is immutable TMP, not a published full Base.
    return body


def validate_scope(root: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((root / "SCOPE.json").read_bytes())
    if (
        body.get("kind") != KIND
        or body.get("version") != "2.3.1"
        or body.get("business_through") != "2026-09-30"
        or body.get("base_manifest_sha256") != DWD
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("full_image_acceptance") is not False
        or body.get("old_value_changes") != 0
        or body.get("source_change_atoms_merged") != 0
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or set(body.get("outputs", {})) != set(TABLES)
    ):
        raise BoundedError("PATCH_SCOPE_CONTRACT_CHANGED")
    # Scope scratch is not retained source or runtime evidence. It may contain
    # failed files: never hide anything under temp inside the copied final scope.
    if _tree(root) != set(body["physical_members"]) | {"SCOPE.json"}:
        raise BoundedError("PATCH_SCOPE_PHYSICAL_SET_CHANGED")
    for name, item in body["physical_members"].items():
        path = _member(root, name)
        if (
            path.stat().st_nlink != 1
            or path.stat().st_size != item["size_bytes"]
            or (file_sha(path) != item["sha256"])
        ):
            raise BoundedError("PATCH_SCOPE_PHYSICAL_BYTES_CHANGED")
    return body


def replay_scope(root: Path, budget: Budget) -> dict[str, Any]:
    """Reproduce qualified selection and exact merge solely from image-local inputs."""
    from .base22_bounded import Limits
    from .base231_holder_revision import replay_representation_tmp
    from .base231_native_delta import prepare_native_delta
    from .base231_source_qualification import audit_qualified_bundle

    scope = validate_scope(root)
    before = root / "inputs/before"
    bundle = root / "inputs/bundle"
    rep = root / "inputs/representation"
    base_manifest = json.loads((before / "manifest.json").read_bytes())
    if (
        file_sha(before / "manifest.json") != DWD
        or file_sha(before / "MATERIALIZATION.json") != base_manifest["materialization_sha256"]
    ):
        raise BoundedError("PATCH_LOCAL_BASE_AUTHORITY_CHANGED")
    descriptor = json.loads((before / "MATERIALIZATION.json").read_bytes())
    rep_body = json.loads((rep / "representation-TMP.json").read_bytes())
    old = sorted(x["sha256"] for x in descriptor["layout"][HOLDING]["files"])
    if (
        sorted(x["sha256"] for x in rep_body["inputs"] if x["role"] == "old") != old
        or rep_body["base_manifest_sha256"] != DWD
        or next(x["sha256"] for x in rep_body["inputs"] if x["role"] == "new.parquet")
        != file_sha(bundle / "domain/projection" / (HOLDING + ".parquet"))
    ):
        raise BoundedError("PATCH_REPRESENTATION_SOURCE_AUTHORITY_CHANGED")
    budget.begin_stage("patch-literal-domain-replay")
    domain = audit_qualified_bundle(bundle, budget)
    limits = Limits(
        allocated_bytes=2 * 1024**3,
        temp_bytes=1024**3,
        free_floor_bytes=budget.limits.free_floor_bytes,
        wall_seconds=1800,
    )
    budget.begin_stage("patch-additive-selection-replay")
    with Budget(budget.root / "additive-replay", [before, bundle], limits) as child:
        additive = prepare_native_delta(base=before, bundle=bundle, output=child.root, budget=child)
    future_sha = next(x["sha256"] for x in rep_body["inputs"] if x["role"] == "future.parquet")
    if future_sha != file_sha(budget.root / "additive-replay/TMP-future-claims.parquet"):
        raise BoundedError("PATCH_FUTURE_SOURCE_DATE_SELECTION_CHANGED")
    budget.begin_stage("patch-representation-replay")
    with Budget(budget.root / "representation-replay", [rep], limits) as child:
        revision = replay_representation_tmp(rep, child)
    budget.begin_stage("patch-final-merge-replay")
    rules = json.loads((root / "inputs/rules.json").read_bytes())
    projected = project_scope(
        before=before,
        additive=budget.root / "additive-replay",
        representation=budget.root / "representation-replay",
        budget=budget,
        read_only_roots=[root],
        rules=rules,
    )
    if projected != scope["outputs"]:
        raise BoundedError("PATCH_RECOMPUTED_ALLOWED_OUTPUT_CHANGED")
    result = dict(
        scope_identity=scope["identity"],
        domain_identity=domain["identity"],
        new_holder_additions=additive["new_holder_components"],
        representation_frames=revision["frames"],
        outputs=projected,
        old_values_changed=0,
        source_change_atoms_merged=0,
    )
    result["identity"] = digest(result)
    return result


def validate_image_binding(root: Path) -> dict[str, Any]:
    """Unchanged 29 tables and exact allowed four-table composition, before replay."""
    scope = validate_scope(root / PREFIX)
    before = json.loads((root / PREFIX / "inputs/before/MATERIALIZATION.json").read_bytes())
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    binding = plan.get("patch_acceptance", {})
    if binding != dict(
        kind=KIND, local_prefix=PREFIX, scope_identity=scope["identity"], base_manifest_sha256=DWD
    ):
        raise BoundedError("PATCH_IMAGE_QUALIFICATION_BINDING_CHANGED")
    expected_tables = set(before["layout"])
    if set(plan.get("tables", {})) != expected_tables:
        raise BoundedError("PATCH_IMAGE_EXACT_TABLE_SET_CHANGED")
    coordinates = ("partition", "rows", "size_bytes", "sha256")
    for table, entry in plan["tables"].items():
        retained = before["layout"][table]["files"] if table != HOLDING else []
        new = scope["outputs"].get(table, [])
        expected = [(tuple(x[k] for k in coordinates), "before23", x["path"]) for x in retained]
        expected += [(tuple(x[k] for k in coordinates), "patch", x["path"]) for x in new]
        actual = [
            (tuple(x[k] for k in coordinates), x["source_id"], x["path"]) for x in entry["files"]
        ]
        if actual != expected:
            raise BoundedError("PATCH_IMAGE_UNAUTHORIZED_DOMAIN_OR_HISTORY_CHANGE")
        # Expose necessary missing/damaged physical output before expensive replay.
        for n, item in enumerate(entry["files"]):
            path = root / f"payload/{table}/{item['partition']}/part-{n:06d}.parquet"
            if not path.is_file() or path.stat().st_nlink != 1:
                raise BoundedError("PATCH_IMAGE_REQUIRED_PHYSICAL_FILE_MISSING")
            if item["source_id"] == "patch" and file_sha(path) != item["sha256"]:
                raise BoundedError("PATCH_IMAGE_PATCH_PHYSICAL_BYTES_CHANGED")
    return scope


def audit_image(root: Path, budget: Budget) -> dict[str, Any]:
    """Full image acceptance: inherited repair, actual patch, all 33 native tables."""
    from .base23_native_image import audit_native_image
    from .base23_required_image import validate_required_binding
    from .base23_required_union import audit_required_union

    scope = validate_image_binding(root)
    local = root / "evidence/required-action-union"
    previous = json.loads((root / PREFIX / "inputs/before/input-plan.json").read_bytes())
    inherited = json.loads((local / "SCOPE.json").read_bytes())
    dependency = json.loads((local / "evidence/required/SCOPE.json").read_bytes())
    validate_required_binding(previous, inherited, dependency)
    budget.begin_stage("inherited-required-domain-replay")
    receipt = audit_required_union(local, budget)
    if receipt["identity"] != previous["required_acceptance"]["replay_identity"]:
        raise BoundedError("PATCH_INHERITED_QUALIFICATION_REPLAY_CHANGED")
    patch = replay_scope(root / PREFIX, budget)
    native = audit_native_image(root, budget)
    result = dict(
        kind="p10_base231_whole_native_image_acceptance_v1",
        scope_identity=scope["identity"],
        inherited_identity=receipt["identity"],
        patch_receipt=patch,
        native_receipt=native,
        consumer_admission="NOT_GRANTED",
    )
    result["identity"] = digest(result)
    if (root / "manifest.json").exists():
        manifest = json.loads((root / "manifest.json").read_bytes())
        if (
            manifest.get("version") != "2.3.1"
            or manifest.get("patch_scan_identity") != result["identity"]
        ):
            raise BoundedError("PATCH_IMAGE_SUCCESSOR_MANIFEST_CHANGED")
    return result


def seal_image(root: Path, receipt: dict[str, Any], *, code_revision: str) -> dict[str, Any]:
    """Write the distinct patch manifest and READY last, never relabel an old seal."""
    from .base23_delivery import PURPOSE
    from .base23_native_image import KIND as NATIVE_KIND

    native = receipt.get("native_receipt", {})
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise BoundedError("PATCH_IMAGE_ALREADY_SEALED")
    if (
        receipt.get("kind") != "p10_base231_whole_native_image_acceptance_v1"
        or receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
        or native.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
        or not native.get("assessment", {}).get("build_eligible")
    ):
        raise BoundedError("PATCH_IMAGE_FULL_SCAN_REQUIRED")
    manifest = dict(
        kind=NATIVE_KIND,
        purpose=PURPOSE,
        version="2.3.1",
        business_through="2026-09-30",
        business_timezone="Asia/Shanghai",
        materialization_sha256=file_sha(root / "MATERIALIZATION.json"),
        scan_identity=native["identity"],
        patch_scan_identity=receipt["identity"],
        code_revision=code_revision,
        assessment=native["assessment"],
        summaries=native["summaries"],
        runtime={"pyarrow": native["pyarrow_version"]},
        independent_acceptance="SEPARATE_RECEIPTS_REQUIRED",
        consumer_admission="NOT_GRANTED",
    )
    manifest["content_digest"] = digest(manifest)
    (root / "manifest.json").open("xb").write(canonical(manifest))
    (root / "READY").open("x").write(file_sha(root / "manifest.json") + "\n")
    return manifest
