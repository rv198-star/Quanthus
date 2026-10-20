"""October dated provider industry observations, never historical PIT backfill.

A whole normalized provider member and its original per-security receipts qualify
only the frozen source cohort. Receipts without response hashes remain without
response hashes; neither provider-update dates nor package times become PIT clocks.
The sealed Base and all legacy September adapters remain unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base23_industry import FIELDS, TABLE

CONTRACT = "p10-base24-weekly-dated-static-industry-v1"
QUALIFICATION = "NORMALIZED_PROVIDER_DATED_SNAPSHOT_NOT_RAW_RECONSTRUCTION_OR_HISTORICAL_PIT"


def snapshot_observation(
    raw: dict[str, Any],
    receipt: dict[str, Any],
    *,
    day: date,
    parent: Any,
    node: dict[str, Any],
    observed_at: datetime,
    source_sha256: str,
    provider_revision: str,
    dataset_revision: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make one new knowledge observation from a date-bound company/node join."""
    if (
        set(raw) != FIELDS
        or type(day) is not date
        or not date(2026, 10, 1) <= day <= date(2026, 10, 31)
        or raw["market_date"] != day.isoformat()
        or not isinstance(raw["stock_code"], str)
        or re.fullmatch(r"\d{6}", raw["stock_code"]) is None
        or raw["security_id"] != raw["stock_code"]
        or raw["source"] != "baostock.query_stock_industry"
        or raw["classification"] != "证监会行业分类"
        or not provider_revision
        or raw["source_revision"] != provider_revision
        or not dataset_revision
        or re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) is None
    ):
        raise BoundedError("BASE24_INDUSTRY_SOURCE_SCOPE_DIFFERS")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if acquired > observed or day > acquired.astimezone(ZoneInfo("Asia/Shanghai")).date():
        raise BoundedError("BASE24_INDUSTRY_CAPTURE_TIME_INVALID")
    try:
        updated = date.fromisoformat(raw["provider_update_date"])
    except (TypeError, ValueError) as exc:
        raise BoundedError("BASE24_INDUSTRY_PROVIDER_UPDATE_DATE_INVALID") from exc
    if updated.isoformat() != raw["provider_update_date"]:
        raise BoundedError("BASE24_INDUSTRY_PROVIDER_UPDATE_DATE_INVALID")
    if (
        receipt.get("dataset") != "industry_membership"
        or receipt.get("security_id") != raw["stock_code"]
        or receipt.get("provider") != "baostock"
        or receipt.get("status") != "success_nonempty"
        or type(receipt.get("response_rows")) is not int
        or receipt["response_rows"] != 1
        or _clock(receipt["acquired_at"]) != acquired
        or json.loads(receipt["request_json"])
        != {"operation": "query_stock_industry", "scope": "all"}
    ):
        raise BoundedError("BASE24_INDUSTRY_ORIGINAL_RECEIPT_DIFFERS")
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    if (
        parent is None
        or not node
        or node.get("node_code") != raw["industry_code"]
        or node.get("node_name") != raw["industry_name"]
        or parent.knowledge_from > observed
        or node["knowledge_from"] > observed
        or node["knowledge_to"] is not None
        and observed >= node["knowledge_to"]
        or node["effective_from"] > start
        or node["effective_to"] is not None
        and start >= node["effective_to"]
    ):
        raise BoundedError("BASE24_INDUSTRY_DATED_PARENT_OR_NODE_UNAVAILABLE")
    row = {
        "record_source": "base24_weekly/dated_static_industry",
        "source_record_id": digest([raw["stock_code"], day, raw["industry_code"]]),
        "source_revision": dataset_revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": observed,
        "knowledge_to": None,
        "effective_from": start,
        "effective_to": None,
        "company_id": parent.company_id,
        "industry_node_id": node["industry_node_id"],
        "membership_type": "PRIMARY_CLASSIFICATION",
        "confidence": None,
        "source_declared_as_of": day,
    }
    return row, {
        "contract_id": CONTRACT,
        "qualification": QUALIFICATION,
        "raw_row_sha256": digest(raw),
        "original_receipt_sha256": digest(receipt),
        "typed_row_sha256": digest(row),
        "original_acquired_at": raw["acquired_at"],
        "actual_acquisition_instant": acquired,
        "original_provider_update_date": raw["provider_update_date"],
        "original_provider_revision": raw["source_revision"],
        "original_dataset_revision": dataset_revision,
        "actual_governance_observation": observed,
        "original_response_sha256": receipt.get("response_sha256"),
        "historical_PIT_qualified": False,
        "native_history_rewritten": False,
        "full_current_market_cohort_qualified": False,
    }


def project_snapshot(
    raw: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    *,
    resolver: Any,
    nodes: list[dict[str, Any]],
    day: date,
    observed_at: datetime,
    source_sha256: str,
    provider_revision: str,
    dataset_revision: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Account for the complete frozen cohort, with no duplicate or ignored rows."""
    if not 0 < len(raw) <= 8192 or len(receipts) != len(raw):
        raise BoundedError("BASE24_INDUSTRY_SOURCE_COHORT_INVALID")
    codes = [r["stock_code"] for r in raw]
    by_code = {r["security_id"]: r for r in receipts}
    by_node = {r["node_code"]: r for r in nodes}
    if (
        len(set(codes)) != len(codes)
        or len(by_code) != len(receipts)
        or set(by_code) != set(codes)
        or len(by_node) != len(nodes)
    ):
        raise BoundedError("BASE24_INDUSTRY_EXACT_KEYS_OR_NODE_VERSIONS_DIFFER")
    rows, proofs = [], []
    for raw_row in raw:
        parent, _ = resolver.resolve_for_report(raw_row["stock_code"], period_end=day)
        row, proof = snapshot_observation(
            raw_row,
            by_code[raw_row["stock_code"]],
            day=day,
            parent=parent,
            node=by_node.get(raw_row["industry_code"], {}),
            observed_at=observed_at,
            source_sha256=source_sha256,
            provider_revision=provider_revision,
            dataset_revision=dataset_revision,
        )
        rows.append(row)
        proofs.append(proof)
    if len({digest([r["company_id"], r["industry_node_id"], day]) for r in rows}) != len(raw):
        raise BoundedError("BASE24_INDUSTRY_OUTPUT_BUSINESS_KEYS_DUPLICATE")
    rows.sort(key=digest)
    return rows, {
        "contract_id": CONTRACT,
        "qualification": QUALIFICATION,
        "table": TABLE,
        "rows": len(rows),
        "source_row_census": len(raw),
        "receipt_census": len(receipts),
        "projection_proof_sha256": digest(proofs),
        "typed_rows_sha256": digest(rows),
        "historical_PIT_qualified": False,
        "native_history_rewritten": False,
        "full_current_market_cohort_qualified": False,
        "missing_original_response_hashes": sum(r.get("response_sha256") is None for r in receipts),
        "consumer_admission": "NOT_GRANTED",
    }


REFERENCE_TABLES = (
    "dwd_security",
    "dwd_security_identifier",
    "dwd_listed_company",
    "dwd_industry_node",
    "dwd_industry_scheme",
    "dwd_trading_venue",
)
SOURCE_NAMES = (
    "industry_membership.parquet",
    "industry_membership.parquet.receipts.parquet",
    "datasets.json",
)
CONTRACT_NAMES = (
    "base22-independent-image.v1.json",
    "dwd-domain-model.v1.json",
    "dwd-domain-model.v1.1-extension.json",
)
KIND = "base24_weekly_normalized_industry_snapshot_v1"


def industry_parent(base: Any, config: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
    """Verify the explicit six-table governance role once and share its resolver."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .financial_reporting import financial_company_resolver_from_listed_company_rows
    from .native_base24_reader import read_native_tables

    role = config.get("weekly_industry_reference", {})
    receipts = role.get("acceptance_receipts", [])
    if (
        config.get("schema_version") != "quanthus.base24-weekly-governance.v1"
        or config.get("purpose") != "isolated_weekly_governance"
        or config.get("consumer_admission") != "NOT_GRANTED"
        or config.get("execution_authorized") is not False
        or role.get("status") != "accepted_reference_only"
        or role.get("tables") != list(REFERENCE_TABLES)
        or role.get("trading_or_research_admission") is not False
        or not isinstance(receipts, list)
        or len(receipts) != 2
        or len(set(receipts)) != 2
        or any(
            not isinstance(s, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", s) is None
            for s in receipts
        )
    ):
        raise BoundedError("BASE24_INDUSTRY_REFERENCE_ROLE_INVALID")
    parent = read_native_tables(base, config, REFERENCE_TABLES)
    rows = {}
    for table in REFERENCE_TABLES:
        paths = parent.files(table)
        if sum(pq.ParquetFile(p).metadata.num_rows for p in paths) > 32768:
            raise BoundedError("BASE24_INDUSTRY_REFERENCE_ROW_BOUND")
        rows[table] = [r for p in paths for r in pq.ParquetFile(p).read().to_pylist()]
    resolver = financial_company_resolver_from_listed_company_rows(
        rows["dwd_security"], rows["dwd_security_identifier"]
    )
    return parent, resolver, rows


def verify_snapshot_scope(root: Any, *, manifest_sha256: str, budget: Any) -> dict[str, Any]:
    """Read only the image, replay all cohort rows and compare physical typed output."""
    from pathlib import Path

    import pyarrow.parquet as pq

    from .base22 import file_sha, load_contract, schemas
    from .base22_sources import _tree
    from .base24_weekly_package import _member

    root = Path(root)
    mp = _member(root, "manifest.json")
    if (
        file_sha(mp) != manifest_sha256
        or _member(root, "READY").read_text().strip() != manifest_sha256
    ):
        raise BoundedError("BASE24_INDUSTRY_READY_IDENTITY_DIFFERS")
    body = json.loads(mp.read_bytes())
    if (
        body.get("kind") != KIND
        or body.get("contract_id") != CONTRACT
        or body.get("qualification") != QUALIFICATION
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("execution_authorized") is not False
        or body.get("complete_weekly_admission") is not False
        or body.get("historical_PIT_admission") is not False
        or body.get("projection_module_sha256") != file_sha(Path(__file__))
    ):
        raise BoundedError("BASE24_INDUSTRY_PURPOSE_OR_CODE_DIFFERS")
    expected = {"weekly-config.json"} | {"sources/" + n for n in SOURCE_NAMES}
    expected |= {"contract/" + n for n in CONTRACT_NAMES}
    expected |= {"references/" + n for n in ("manifest.json", "MATERIALIZATION.json", "READY")}
    mat = json.loads(_member(root, "references/MATERIALIZATION.json").read_bytes())
    expected |= {
        "references/" + e["path"] for t in REFERENCE_TABLES for e in mat["layout"][t]["files"]
    }
    expected.add("tables/" + TABLE + ".parquet")
    if set(body["files"]) != expected or _tree(root) != expected | {"manifest.json", "READY"}:
        raise BoundedError("BASE24_INDUSTRY_EXACT_PHYSICAL_CLOSURE_DIFFERS")
    for name, entry in body["files"].items():
        p = _member(root, name)
        if p.stat().st_size != entry["bytes"] or file_sha(p) != entry["sha256"]:
            raise BoundedError("BASE24_INDUSTRY_PHYSICAL_HASH_DIFFERS")
        budget.check()
    config = json.loads(_member(root, "weekly-config.json").read_bytes())
    parent, resolver, refs = industry_parent(root / "references", config)
    if (
        config.get("industry_snapshot_source", {}).get("provider_revision")
        != body["provider_revision"]
        or config.get("industry_snapshot_source", {}).get("qualification") != QUALIFICATION
    ):
        raise BoundedError("BASE24_INDUSTRY_PROVIDER_CONTRACT_DIFFERS")
    if dict(parent.binding) != body["base_binding"]:
        raise BoundedError("BASE24_INDUSTRY_BASE_BINDING_DIFFERS")
    selected = [
        r
        for r in json.loads((root / "sources/datasets.json").read_bytes())["datasets"]
        if r["name"] == "industry_membership"
    ]
    if len(selected) != 1:
        raise BoundedError("BASE24_INDUSTRY_DATASET_CONTROL_DIFFERS")
    control = selected[0]
    request = json.loads(control["source_request"])
    if (
        control.get("status") != "ready"
        or control.get("sha256") != body["files"]["sources/industry_membership.parquet"]["sha256"]
        or control.get("source_revision") != body["dataset_revision"]
        or control.get("watermark") != body["business_date"]
        or type(control.get("row_count")) is not int
        or not 0 < control["row_count"] <= 8192
        or request.get("receipt_sha256")
        != body["files"]["sources/industry_membership.parquet.receipts.parquet"]["sha256"]
        or request.get("receipt_count") != control["row_count"]
        or request.get("provider_metadata", {}).get("provider_revision")
        != body["provider_revision"]
    ):
        raise BoundedError("BASE24_INDUSTRY_DATASET_CONTROL_DIFFERS")
    inputs = []
    for name in SOURCE_NAMES[:2]:
        pf = pq.ParquetFile(root / "sources" / name)
        if pf.metadata.num_rows != control["row_count"]:
            raise BoundedError("BASE24_INDUSTRY_SOURCE_COHORT_INVALID")
        inputs.append(pf.read().to_pylist())
    rows, proof = project_snapshot(
        inputs[0],
        inputs[1],
        resolver=resolver,
        nodes=refs["dwd_industry_node"],
        day=date.fromisoformat(body["business_date"]),
        observed_at=datetime.fromisoformat(body["observed_at"]),
        source_sha256=control["sha256"],
        provider_revision=body["provider_revision"],
        dataset_revision=body["dataset_revision"],
    )
    _, model, extension = load_contract(root / "contract")
    schema = schemas(model, extension)[TABLE]
    physical = pq.ParquetFile(root / "tables" / (TABLE + ".parquet"))
    if not physical.schema_arrow.equals(schema, check_metadata=True):
        raise BoundedError("BASE24_INDUSTRY_PHYSICAL_SCHEMA_DIFFERS")
    actual = physical.read()
    if (
        actual.to_pylist() != rows
        or proof != body["projection"]
        or any(not field.nullable and actual.column(field.name).null_count for field in schema)
        or not {r["company_id"] for r in rows}
        <= {r["company_id"] for r in refs["dwd_listed_company"]}
        or not {r["industry_scheme_id"] for r in refs["dwd_industry_node"]}
        <= {r["industry_scheme_id"] for r in refs["dwd_industry_scheme"]}
    ):
        raise BoundedError("BASE24_INDUSTRY_PHYSICAL_SEMANTICS_OR_REFERENCE_DIFFERS")
    budget.check()
    return {
        "status": "PASS_DATED_INDUSTRY_COHORT_ONLY",
        "manifest_sha256": manifest_sha256,
        "projection": proof,
        "source_cohort_only": True,
        "consumer_admission": "NOT_GRANTED",
    }
