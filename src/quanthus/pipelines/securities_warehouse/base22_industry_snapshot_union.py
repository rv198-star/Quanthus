"""Retained dated industry snapshots, never historical industry backfill.

One immutable native dependency is shared by all selected weekly snapshots. The
native 5,201 memberships remain unchanged; newly dated snapshots carry their
retained acquisition and source-declared dates. Static observations do not become
historical PIT evidence or resolve the separate 50-exception governance scope.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import industry_snapshot_observation
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows

CONTRACT_ID = "p10-base22-retained-industry-snapshot-native-union-v1"
TABLE = "dwd_company_industry_membership"
TABLES = {
    TABLE, "dwd_industry_scheme", "dwd_industry_node", "dwd_listed_company",
    "dwd_security", "dwd_security_identifier",
}
MAX_RAW_ROWS = 8192


def project_snapshot(
    raw: list[dict[str, Any]],
    *,
    day: date,
    resolver: Any,
    nodes: list[dict[str, Any]],
    source_sha256: str,
    revision: str,
) -> list[dict[str, Any]]:
    """Exact date/code/name/classification, no alias or current-to-past fallback."""
    if type(day) is not date or not date(2026, 8, 1) <= day <= THROUGH:
        raise BoundedError("INDUSTRY_SNAPSHOT_BUSINESS_SCOPE_INVALID")
    if not raw or len(raw) > MAX_RAW_ROWS or len({r["stock_code"] for r in raw}) != len(raw):
        raise BoundedError("INDUSTRY_SNAPSHOT_EXACT_SOURCE_KEY_CENSUS_INVALID")
    node_by_code = {r["node_code"]: r for r in nodes}
    if len(node_by_code) != len(nodes):
        raise BoundedError("INDUSTRY_SNAPSHOT_NODE_VERSION_NOT_UNIQUE")
    output = []
    for row in raw:
        if row["market_date"] != str(day) or row["classification"] != "证监会行业分类":
            raise BoundedError("INDUSTRY_SNAPSHOT_DATE_OR_CLASSIFICATION_CHANGED")
        node = node_by_code.get(row["industry_code"])
        if node is None or node["node_name"] != row["industry_name"]:
            raise BoundedError("INDUSTRY_SNAPSHOT_EXACT_NODE_CODE_NAME_UNAVAILABLE")
        parent, _ = resolver.resolve_for_report(row["stock_code"], period_end=day)
        if parent is None:
            raise BoundedError("INDUSTRY_SNAPSHOT_DATED_COMPANY_UNAVAILABLE")
        output.extend(industry_snapshot_observation(
            row, company_id=parent.company_id, node=node,
            parent_knowledge=parent.knowledge_from,
            source_sha256=source_sha256, revision=revision,
        )[TABLE])
    if len(output) != len(raw) or len({digest([r["company_id"], r["industry_node_id"],
                                             r["effective_from"]]) for r in output}) != len(raw):
        raise BoundedError("INDUSTRY_SNAPSHOT_OUTPUT_BUSINESS_KEY_CENSUS_INVALID")
    return output


def audit_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Reproduce all retained raw snapshots and scan the full native union offline."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("purpose") != "P10 dated static industry snapshot native union"
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(body.get(k) is not False
               for k in ("READY", "complete_Base22", "historical_PIT_admission"))
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or set(body.get("outputs", {})) != TABLES
        or len(body.get("weekly_sources", [])) != 4
    ):
        raise BoundedError("INDUSTRY_SNAPSHOT_OFFLINE_SCOPE_INVALID")
    if _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("INDUSTRY_SNAPSHOT_PHYSICAL_FILE_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("INDUSTRY_SNAPSHOT_PHYSICAL_MEMBER_CHANGED")

    def read(rel: str, bound: int) -> list[dict[str, Any]]:
        p = _physical(root, rel, independent=True)
        pf = pq.ParquetFile(p)
        if pf.metadata.num_rows > bound:
            raise BoundedError("INDUSTRY_SNAPSHOT_LOCAL_PARENT_OR_RAW_BOUND")
        budget.check()
        return list(pf.read().to_pylist())

    c, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    manifest = json.loads((root / body["original_native_manifest"]).read_bytes())
    parents, summaries = {}, {}
    for table, item in body["outputs"].items():
        native_rel = body["original_membership"] if table == TABLE else item["path"]
        native = next((i for i in manifest["files"] if i["path"] == item["native_path"]), None)
        if native is None or native["sha256"] != file_sha(root / native_rel):
            raise BoundedError("INDUSTRY_SNAPSHOT_ORIGINAL_NATIVE_BYTES_DIFFER")
        summaries[table] = semantic_scan([root / item["path"]], ss[table], check=budget.check)
        if summaries[table] != item["semantic_scan"]:
            raise BoundedError("INDUSTRY_SNAPSHOT_RECOMPUTED_TYPED_SUMMARY_CHANGED")
        parents[table] = read(item["path"], 32768)
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    expected = read(body["original_membership"], 8192)
    if len(expected) != body["native_membership_rows"]:
        raise BoundedError("INDUSTRY_SNAPSHOT_ORIGINAL_MEMBER_CENSUS_CHANGED")
    snapshot_proofs = []
    for item in body["weekly_sources"]:
        release = json.loads((root / item["manifest"]).read_bytes())
        if release.get("status") != "ready" or (
            (root / item["READY"]).read_text().strip() != file_sha(root / item["manifest"])
        ):
            raise BoundedError("INDUSTRY_SNAPSHOT_LOCAL_RELEASE_READY_INVALID")
        selected = [a for a in release["artifacts"] if a["name"] == "industry_membership"]
        if len(selected) != 1 or selected[0]["sha256"] != file_sha(root / item["raw"]):
            raise BoundedError("INDUSTRY_SNAPSHOT_LOCAL_RAW_IDENTITY_CHANGED")
        raw = read(item["raw"], MAX_RAW_ROWS)
        output = project_snapshot(
            raw, day=date.fromisoformat(release["market_date"]), resolver=resolver,
            nodes=parents["dwd_industry_node"], source_sha256=selected[0]["sha256"],
            revision=release["release_id"],
        )
        if len(output) != item["rows"]:
            raise BoundedError("INDUSTRY_SNAPSHOT_LOCAL_OUTPUT_ROW_CENSUS_CHANGED")
        expected.extend(output)
        snapshot_proofs.append({
            "release_id": release["release_id"], "rows": len(output),
            "raw_rows_identity": digest(raw), "typed_rows_identity": digest(output),
        })
    if not pa.Table.from_pylist(expected, schema=ss[TABLE]).equals(
        pq.ParquetFile(root / body["outputs"][TABLE]["path"]).read()
    ) or digest(snapshot_proofs) != body["snapshot_proofs_identity"]:
        raise BoundedError("INDUSTRY_SNAPSHOT_ORIGINAL_OR_PROJECTED_ATOMS_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in sorted(TABLES):
            scan.add(table, [root / body["outputs"][table]["path"]])
            rule = c["tables"][table]
            scan.keys(table, rule["business_key"], rule["version_key"])
            scan.row_rules(table, ss[table], rule)
            scan.lineage(table, [tuple(x) for x in body["source_registries"][table]])
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in TABLES]},
            {"tables": {t: c["tables"][t] for t in TABLES}},
        )
    result = {
        "status": "SCOPED_DATED_STATIC_INDUSTRY_NATIVE_UNION_PASS_NOT_HISTORICAL_PIT_OR_BASE22",
        "scope_identity": body["identity"],
        "rows": {t: s["rows"] for t, s in summaries.items()},
        "native_typed_summaries": summaries,
        "snapshot_proofs_identity": digest(snapshot_proofs),
        "native_memberships_preserved": body["native_membership_rows"],
        "new_dated_snapshots": len(expected) - body["native_membership_rows"],
        "historical_memberships_inferred": 0,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
