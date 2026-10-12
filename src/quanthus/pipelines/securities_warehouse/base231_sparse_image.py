"""Exact additive financial successor of an independently scanned physical image.

The prior patch evaluator and sparse source qualification remain immutable.
Only source-replayed, previously absent financial identifiers may be appended.
Cold acceptance replays both inherited repairs and the new sparse domain, then
scans all native tables. Archived source roots are never opened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_sources import _member, _tree
from .base231_image import PREFIX as OLD_PREFIX
from .base231_image import partition_files, replay_scope, validate_scope
from .base231_sparse_pack import audit as audit_sparse
from .financial_reporting import financial_reporting_schemas

PREFIX = "evidence/base231-sparse"
KIND = "p10_base231_exact_sparse_financial_successor_v1"
PREDECESSOR = "sha256:ecc52ff2ac9b88b5b7146e00b6c53160e2d4cce895d6d71a0dc7e2527a945930"
SPARSE = "sha256:0fb0bd81dff1ecef69433ad7b28dfff153d9d1206a3763f661c56f8f6d8723f1"
KEYS = {t: t.removeprefix("dwd_") + "_id" for t in financial_reporting_schemas()}
EXPECTED = {"dwd_financial_report": 19, "dwd_financial_concept": 0, "dwd_financial_fact": 304}


def select_absent(scan: GlobalScan, table: str, schema: Any) -> dict[str, int]:
    """Keep equal source atoms unchanged; any ambiguous overlap fails closed.

    Governance observation clocks may differ between two parser runs. Every
    other field, including original source identity, time and Decimal, must
    agree. There is no last-writer-wins policy or inferred provider acquisition.
    """
    key = KEYS[table]
    prior, new = "prior_" + table, "sparse_" + table
    old = "overlap_" + table
    scan.connection.execute(
        f"CREATE VIEW {old} AS SELECT o.* FROM {prior} o "
        f'WHERE o."{key}" IN (SELECT "{key}" FROM {new})'
    )
    for view in [new]:
        scan.reject(
            f'SELECT "{key}" FROM {view} GROUP BY "{key}" '
            f'HAVING count(*)>1 OR "{key}" IS NULL LIMIT 1',
            "SPARSE_IMAGE_AMBIGUOUS_IDENTIFIER",
        )
    fields = [n for n in schema.names if n not in {"knowledge_from", "knowledge_to"}]
    conflict = " OR ".join(f'n."{n}" IS DISTINCT FROM o."{n}"' for n in fields)
    scan.reject(
        f'SELECT 1 FROM {new} n JOIN {old} o USING("{key}") WHERE {conflict} LIMIT 1',
        "SPARSE_IMAGE_OVERLAPPING_ATOM_CONFLICT",
    )
    scan.connection.execute(
        f"CREATE VIEW absent_{table} AS SELECT n.* FROM {new} n "
        f"WHERE NOT EXISTS(SELECT 1 FROM {prior} o "
        f'WHERE n."{key}"=o."{key}")'
    )
    result = scan.connection.execute(
        f"SELECT (SELECT count(*) FROM {new}), (SELECT count(*) FROM absent_{table})"
    ).fetchone()
    assert result is not None
    return {
        "source_rows": result[0],
        "new_identifiers": result[1],
        "equal_existing_identifiers": result[0] - result[1],
    }


def project_delta(
    *,
    before: Path,
    sparse: Path,
    rules: dict[str, Any],
    budget: Budget,
    read_only_roots: list[Path],
    prior_descriptor: Path | None = None,
) -> dict[str, Any]:
    """One bounded selection shared by build and offline replay."""
    layout = json.loads((prior_descriptor or before / "MATERIALIZATION.json").read_bytes())[
        "layout"
    ]
    ss = financial_reporting_schemas()
    outputs, census = {}, {}
    with GlobalScan(budget, read_only_roots=read_only_roots) as scan:
        for table, schema in ss.items():
            scan.add("prior_" + table, [_member(before, m["path"]) for m in layout[table]["files"]])
            scan.add("sparse_" + table, [sparse / "projection" / (table + ".parquet")])
            census[table] = select_absent(scan, table, schema)
            if census[table]["new_identifiers"] != EXPECTED[table]:
                raise BoundedError("SPARSE_IMAGE_EXACT_ALLOWED_KEY_CENSUS_CHANGED")
            if EXPECTED[table]:
                reader = scan.connection.execute(
                    f'SELECT * FROM absent_{table} ORDER BY "{KEYS[table]}"'
                ).to_arrow_reader(budget.limits.batch_rows)
                outputs[table] = partition_files(
                    reader, table=table, rule=rules["tables"][table], schema=schema, budget=budget
                )
    return {
        "outputs": outputs,
        "census": census,
        "old_value_changes": 0,
        "old_history_deleted": 0,
        "new_provider_or_publication_times_inferred": 0,
    }


def write_scope(root: Path, delta: dict[str, Any], *, code_revision: str) -> dict[str, Any]:
    if set(delta["outputs"]) != {t for t in EXPECTED if EXPECTED[t]}:
        raise BoundedError("SPARSE_IMAGE_EXACT_DOMAIN_SET_CHANGED")
    files = {
        n: {"size_bytes": (root / n).stat().st_size, "sha256": file_sha(root / n)}
        for n in sorted(_tree(root))
    }
    body = {
        "kind": KIND,
        "business_through": "2026-09-30",
        "version": "2.3.1",
        "predecessor_manifest_sha256": PREDECESSOR,
        "sparse_manifest_sha256": SPARSE,
        "consumer_admission": "NOT_GRANTED",
        "delta": delta,
        "outputs": delta["outputs"],
        "physical_members": files,
        "evaluator_sha256": file_sha(Path(__file__)),
        "code_revision": code_revision,
    }
    body["identity"] = digest(body)
    (root / "SCOPE.json").open("xb").write(canonical(body))
    validate_local_scope(root)
    return body


def validate_local_scope(root: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((root / "SCOPE.json").read_bytes())
    if (
        body.get("kind") != KIND
        or body.get("version") != "2.3.1"
        or body.get("business_through") != "2026-09-30"
        or body.get("predecessor_manifest_sha256") != PREDECESSOR
        or body.get("sparse_manifest_sha256") != SPARSE
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("outputs") != body.get("delta", {}).get("outputs")
        or set(body.get("outputs", {})) != {t for t in EXPECTED if EXPECTED[t]}
        or any(
            body.get("delta", {}).get(k) != 0
            for k in (
                "old_value_changes",
                "old_history_deleted",
                "new_provider_or_publication_times_inferred",
            )
        )
    ):
        raise BoundedError("SPARSE_IMAGE_SCOPE_CONTRACT_CHANGED")
    if _tree(root) != set(body["physical_members"]) | {"SCOPE.json"}:
        raise BoundedError("SPARSE_IMAGE_SCOPE_PHYSICAL_SET_CHANGED")
    for name, item in body["physical_members"].items():
        p = _member(root, name)
        if (
            not p.is_file()
            or p.stat().st_nlink != 1
            or p.stat().st_size != item["size_bytes"]
            or file_sha(p) != item["sha256"]
        ):
            raise BoundedError("SPARSE_IMAGE_SCOPE_PHYSICAL_BYTES_CHANGED")
    if (
        file_sha(root / "inputs/predecessor/manifest.json") != PREDECESSOR
        or file_sha(root / "inputs/sparse/manifest.json") != SPARSE
    ):
        raise BoundedError("SPARSE_IMAGE_ORIGINAL_AUTHORITY_CHANGED")
    parent = root / "inputs/predecessor"
    manifest = json.loads((parent / "manifest.json").read_bytes())
    if file_sha(parent / "MATERIALIZATION.json") != manifest["materialization_sha256"]:
        raise BoundedError("SPARSE_IMAGE_PARENT_DESCRIPTOR_CHANGED")
    descriptor = json.loads((parent / "MATERIALIZATION.json").read_bytes())
    if file_sha(parent / "input-plan.json") != descriptor["evidence"]["evidence/input-plan.json"]:
        raise BoundedError("SPARSE_IMAGE_PARENT_PLAN_CHANGED")
    return body


def validate_binding(root: Path) -> dict[str, Any]:
    scope = validate_local_scope(root / PREFIX)
    parent = json.loads((root / PREFIX / "inputs/predecessor/MATERIALIZATION.json").read_bytes())
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    if plan.get("sparse_acceptance") != {
        "kind": KIND,
        "local_prefix": PREFIX,
        "scope_identity": scope["identity"],
        "predecessor_manifest_sha256": PREDECESSOR,
    } or (set(plan["tables"]) != set(parent["layout"])):
        raise BoundedError("SPARSE_IMAGE_QUALIFICATION_BINDING_CHANGED")
    coords = ("path", "partition", "rows", "size_bytes", "sha256", "source_id")
    for table, layout in parent["layout"].items():
        retained = [{**m, "source_id": "predecessor"} for m in layout["files"]]
        extra = [{**m, "source_id": "sparse"} for m in scope["outputs"].get(table, [])]
        expected = [tuple(m[k] for k in coords) for m in retained + extra]
        if [tuple(m[k] for k in coords) for m in plan["tables"][table]["files"]] != expected:
            raise BoundedError("SPARSE_IMAGE_UNAUTHORIZED_HISTORY_OR_OUTPUT_CHANGE")
        for n, m in enumerate(retained + extra):
            p = root / f"payload/{table}/{m['partition']}/part-{n:06d}.parquet"
            if not p.is_file() or p.stat().st_nlink != 1:
                raise BoundedError("SPARSE_IMAGE_REQUIRED_PHYSICAL_FILE_MISSING")
            if m["source_id"] == "sparse" and file_sha(p) != m["sha256"]:
                raise BoundedError("SPARSE_IMAGE_OUTPUT_PHYSICAL_BYTES_CHANGED")
    # Every inherited evidence member must be byte-identical except the three
    # explicitly regenerated selection/registry/limitation files. No old repairs
    # or their original contracts are replaced by a new receipt.
    inherited = {
        n: sha
        for n, sha in parent["evidence"].items()
        if n not in {"evidence/input-plan.json", "evidence/limitations.json"}
        and not n.startswith("evidence/sources/")
    }
    for name, sha in inherited.items():
        if file_sha(_member(root, name)) != sha:
            raise BoundedError("SPARSE_IMAGE_INHERITED_EVIDENCE_CHANGED")
    return scope


def audit_image(root: Path, budget: Budget) -> dict[str, Any]:
    from .base23_native_image import audit_native_image
    from .base23_required_image import validate_required_binding
    from .base23_required_union import audit_required_union

    scope = validate_binding(root)
    validate_scope(root / OLD_PREFIX)
    old_plan = json.loads((root / OLD_PREFIX / "inputs/before/input-plan.json").read_bytes())
    local = root / "evidence/required-action-union"
    inherited = json.loads((local / "SCOPE.json").read_bytes())
    dependency = json.loads((local / "evidence/required/SCOPE.json").read_bytes())
    validate_required_binding(old_plan, inherited, dependency)
    budget.begin_stage("inherited-required-domain-replay")
    required = audit_required_union(local, budget)
    if required["identity"] != old_plan["required_acceptance"]["replay_identity"]:
        raise BoundedError("SPARSE_IMAGE_INHERITED_REPLAY_CHANGED")
    patch = replay_scope(root / OLD_PREFIX, budget)
    budget.begin_stage("sparse-original-source-replay")
    with Budget(budget.root / "sparse-replay", [root], budget.limits) as child:
        sparse = audit_sparse(root / PREFIX / "inputs/sparse", child)
    # Read only the retained predecessor file coordinates inside this image.
    # No financial payload is duplicated as evidence or opened in an old root.
    budget.begin_stage("sparse-exact-additive-selection-replay")
    rules = json.loads((root / "contract/base22-independent-image.v1.json").read_bytes())
    with Budget(budget.root / "delta-replay", [root], budget.limits) as child:
        delta = project_delta(
            before=root,
            sparse=root / PREFIX / "inputs/sparse",
            rules=rules,
            budget=child,
            read_only_roots=[root],
            prior_descriptor=root / PREFIX / "inputs/predecessor/MATERIALIZATION.json",
        )
    if delta != scope["delta"]:
        raise BoundedError("SPARSE_IMAGE_RECOMPUTED_ALLOWED_DELTA_CHANGED")
    native = audit_native_image(root, budget)
    result = {
        "kind": KIND,
        "scope_identity": scope["identity"],
        "inherited_identity": required["identity"],
        "patch_receipt": patch,
        "sparse_receipt": sparse,
        "delta": delta,
        "native_receipt": native,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    if (root / "manifest.json").exists():
        manifest = json.loads((root / "manifest.json").read_bytes())
        if (
            manifest.get("sparse_scan_identity") != result["identity"]
            or manifest.get("version") != "2.3.1"
        ):
            raise BoundedError("SPARSE_IMAGE_RECOMPUTED_SUCCESSOR_MANIFEST_CHANGED")
    return result


def seal_image(root: Path, receipt: dict[str, Any], *, code_revision: str) -> dict[str, Any]:
    from .base23_delivery import PURPOSE

    if receipt.get("kind") != KIND or receipt.get("identity") != digest(
        {k: v for k, v in receipt.items() if k != "identity"}
    ):
        raise BoundedError("SPARSE_IMAGE_FULL_SCAN_REQUIRED")
    # Reuse the validated native seal shape without ever writing a temporary
    # wrong version or READY. Native acceptance checks this shape independently.
    native = receipt["native_receipt"]
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise BoundedError("SPARSE_IMAGE_ALREADY_SEALED")
    from .base23_native_image import KIND as NATIVE_KIND

    if (
        not native.get("assessment", {}).get("build_eligible")
        or native.get("kind") != NATIVE_KIND
        or native.get("identity") != digest({k: v for k, v in native.items() if k != "identity"})
        or native.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
    ):
        raise BoundedError("SPARSE_IMAGE_FULL_SCAN_REQUIRED")
    manifest = {
        "kind": native["kind"],
        "purpose": PURPOSE,
        "version": "2.3.1",
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "materialization_sha256": native["materialization_sha256"],
        "scan_identity": native["identity"],
        "sparse_scan_identity": receipt["identity"],
        "code_revision": code_revision,
        "assessment": native["assessment"],
        "summaries": native["summaries"],
        "runtime": {"pyarrow": native["pyarrow_version"]},
        "independent_acceptance": "SEPARATE_RECEIPTS_REQUIRED",
        "consumer_admission": "NOT_GRANTED",
    }
    manifest["content_digest"] = digest(manifest)
    (root / "manifest.json").open("xb").write(canonical(manifest))
    (root / "READY").open("x").write(file_sha(root / "manifest.json") + "\n")
    return manifest
