"""Reuse an independently qualified financial origin for new statement literals.

Only the prelisting financial-report interval is admitted. The origin is not a
legal-form, trading-identity or historical PIT extension. Source and parent
closure stay local and replayable; unchanged listed-period outputs are separate.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha, load_contract
from .base22_bounded import BoundedError, Budget, GlobalScan, Limits, semantic_scan
from .base22_financial import TABLES
from .base22_sources import _tree, physical_path
from .base231_current_statement import (
    CONTRACT_ID as STATEMENT_CONTRACT,
)
from .base231_current_statement import (
    ConceptAccumulator,
    current_statement,
    wrap_current_envelope,
)
from .base231_official_profile import SCOPE, replay_origin_scope
from .financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    FinancialStatementSource,
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
)

CONTRACT_ID = "p10-base231-current-origin-bounded-prelisting-statements-v1"


def financial_origin_identity(
    company: dict[str, Any], parent: FinancialCompanyIdentity
) -> FinancialCompanyIdentity:
    if (
        company.get("resolution_scope") != SCOPE
        or company.get("company_type") != "official_source_scoped_financial_issuer"
        or company.get("record_source") != "cninfo.company_profile.reported_origin"
        or company.get("company_id") != parent.company_id
        or company["effective_from"] >= parent.effective_from
        or company["knowledge_from"] < parent.knowledge_from
    ):
        raise BoundedError("ORIGIN_STATEMENT_QUALIFIED_FINANCIAL_SCOPE_REQUIRED")
    return FinancialCompanyIdentity(
        stock_code=parent.stock_code,
        security_id=parent.security_id,
        company_id=parent.company_id,
        effective_from=company["effective_from"],
        effective_to=parent.effective_from,
        knowledge_from=company["knowledge_from"],
    )


def origin_statement(
    raw: dict[str, Any],
    *,
    source: FinancialStatementSource,
    company: dict[str, Any],
    parent: FinancialCompanyIdentity,
    observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    identity = financial_origin_identity(company, parent)
    if raw["stock_code"] != identity.stock_code:
        raise BoundedError("ORIGIN_STATEMENT_EXACT_CODE_REQUIRED")
    output, proof = current_statement(
        raw,
        source=source,
        resolver=FinancialCompanyResolver([identity]),
        observed_at=observed_at,
    )
    return output, dict(
        contract_id=CONTRACT_ID,
        identity_sha256=digest(identity.__dict__),
        company_observation_sha256=digest(company),
        statement_proof=proof,
        financial_scope_only=True,
        security_or_listing_extended=False,
        historical_PIT_qualified=False,
    )


def iter_origin_statements(root: Path, plan: dict[str, Any]) -> Iterator[Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    dependency = root / "origin"
    if file_sha(dependency / "INPUTS.json") != plan["origin_plan_sha256"]:
        raise BoundedError("ORIGIN_STATEMENT_PARENT_PLAN_CHANGED")
    replay_origin_scope(dependency)
    origin_plan = json.loads((dependency / "INPUTS.json").read_bytes())
    companies = pq.ParquetFile(dependency / "outputs/dwd_listed_company.parquet").read().to_pylist()
    by_company = {row["company_id"]: row for row in companies}
    if len(by_company) != len(companies):
        raise BoundedError("ORIGIN_STATEMENT_AMBIGUOUS_COMPANY")
    parents = {
        table: [
            row for name in names for row in pq.ParquetFile(dependency / name).read().to_pylist()
        ]
        for table, names in origin_plan["parents"].items()
    }
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    codes = {spec["code"] for spec in origin_plan["profiles"]}
    registration = json.loads((root / "inputs/collection-registration.json").read_bytes())
    execution = json.loads((root / "inputs/collection-execution.json").read_bytes())
    if execution["registration_sha256"] != file_sha(root / "inputs/collection-registration.json"):
        raise BoundedError("ORIGIN_STATEMENT_COLLECTION_CHANGED")
    targets = {r["label"]: r for r in registration["targets"] if r["code"] in codes}
    receipts = {r["label"]: r for r in execution["receipts"]}
    selection = plan["selection"]
    if len(selection) != len(targets) or {s["label"] for s in selection} != set(targets):
        raise BoundedError("ORIGIN_STATEMENT_EXACT_SOURCE_SET_CHANGED")
    now = datetime.fromisoformat(plan["observed_at"])
    for spec in selection:
        label = spec["label"]
        receipt = json.loads((root / spec["receipt"]).read_bytes())
        if (
            receipt != receipts[label]
            or any(receipt[k] != targets[label][k] for k in ("code", "provider_source", "URI"))
            or receipt["status"] != "FULL_CURRENT_ENVELOPE_PENDING_QUALIFICATION"
        ):
            raise BoundedError("ORIGIN_STATEMENT_EXACT_RECEIPT_CHANGED")
        wrapped = wrap_current_envelope((root / spec["body"]).read_bytes(), receipt)
        if pq.ParquetFile(root / spec["wrapper"]).read().to_pylist() != [wrapped]:
            raise BoundedError("ORIGIN_STATEMENT_RAW_WRAPPER_CHANGED")
        parent, _ = resolver.resolve_for_report(wrapped["stock_code"], period_end=date(2026, 9, 30))
        if parent is None or parent.company_id not in by_company:
            raise BoundedError("ORIGIN_STATEMENT_NATIVE_PARENT_MISSING")
        source = FinancialStatementSource(
            "statements",
            root / spec["wrapper"],
            spec["wrapper"],
            file_sha(root / spec["wrapper"]),
            STATEMENT_CONTRACT,
            "parent",
        )
        output, proof = origin_statement(
            wrapped,
            source=source,
            company=by_company[parent.company_id],
            parent=parent,
            observed_at=now,
        )
        yield spec, output, proof


def replay_origin_statements(root: Path, scratch: Path) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("complete_Base") is not False
        or plan.get("historical_PIT_qualified") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("ORIGIN_STATEMENT_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("ORIGIN_STATEMENT_PHYSICAL_SET_CHANGED")
    for name, member in plan["members"].items():
        p = physical_path(root / name)
        if (
            root.resolve() not in p.parents
            or p.stat().st_nlink != 1
            or (p.stat().st_size != member["bytes"] or file_sha(p) != member["sha256"])
        ):
            raise BoundedError("ORIGIN_STATEMENT_LOCAL_MEMBER_CHANGED")
    for name, sha in plan["code_inputs"].items():
        if file_sha(Path(name)) != sha:
            raise BoundedError("ORIGIN_STATEMENT_ORDINARY_CODE_CHANGED")
    ss = financial_reporting_schemas()
    if {p.stem for p in (root / "outputs").glob("*.parquet")} != set(TABLES):
        raise BoundedError("ORIGIN_STATEMENT_EXACT_TABLE_SET_CHANGED")
    limits = Limits(
        free_floor_bytes=8 * 1024**3,
        allocated_bytes=1024**3,
        temp_bytes=512 * 1024**2,
        wall_seconds=3600,
    )
    counts: Counter[str] = Counter()
    concepts = ConceptAccumulator()
    proof_hash = hashlib.sha256()
    with Budget(scratch, [root], limits) as budget:
        writers = {
            t: pq.ParquetWriter(scratch / (t + ".parquet"), ss[t], compression="zstd")
            for t in TABLES
            if t != "dwd_financial_concept"
        }
        try:
            for spec, output, proof in iter_origin_statements(root, plan):
                budget.check()
                proof_hash.update(canonical(dict(label=spec["label"], evidence=proof)) + b"\n")
                concepts.add(output["dwd_financial_concept"])
                for table, writer in writers.items():
                    native = pa.Table.from_pylist(output[table], schema=ss[table])
                    writer.write_table(native)
                    counts[table] += native.num_rows
        finally:
            for writer in writers.values():
                writer.close()
        values, closure = concepts.finish()
        pq.write_table(
            pa.Table.from_pylist(values, schema=ss["dwd_financial_concept"]),
            scratch / "dwd_financial_concept.parquet",
            compression="zstd",
        )
        counts["dwd_financial_concept"] = len(values)
        if (
            dict(counts) != plan["rows"]
            or digest(closure) != plan["concept_proof_sha256"]
            or ("sha256:" + proof_hash.hexdigest() != plan["proof_stream_sha256"])
        ):
            raise BoundedError("ORIGIN_STATEMENT_RECONSTRUCTION_CHANGED")
        contract, _, _ = load_contract(root / "config")
        with GlobalScan(budget, read_only_roots=[root]) as scan:
            for table in TABLES:
                actual = root / "outputs" / (table + ".parquet")
                if pq.ParquetFile(actual).schema_arrow != ss[table]:
                    raise BoundedError("ORIGIN_STATEMENT_NATIVE_SCHEMA_CHANGED")
                scan.add(table, [actual])
                scan.add("e_" + table, [scratch / (table + ".parquet")])
                for left, right in [(table, "e_" + table), ("e_" + table, table)]:
                    scan.reject(
                        "SELECT 1 FROM (SELECT * FROM "
                        + left
                        + " EXCEPT ALL SELECT * FROM "
                        + right
                        + ") LIMIT 1",
                        "ORIGIN_STATEMENT_SERIALIZED_ROWS_CHANGED",
                    )
                keys = contract["tables"][table]["version_key"]
                scan.reject(
                    "SELECT 1 FROM "
                    + table
                    + " GROUP BY "
                    + ",".join(keys)
                    + " HAVING count(*)>1 LIMIT 1",
                    "ORIGIN_STATEMENT_DUPLICATE_VERSION",
                )
            scan.reject(
                "SELECT 1 FROM dwd_financial_fact f LEFT JOIN dwd_financial_report r "
                "USING(financial_report_id) WHERE r.financial_report_id IS NULL LIMIT 1",
                "ORIGIN_STATEMENT_REPORT_REFERENCE_MISSING",
            )
            scan.reject(
                "SELECT 1 FROM dwd_financial_fact f WHERE NOT EXISTS "
                "(SELECT 1 FROM dwd_financial_concept c WHERE "
                "c.financial_concept_id=f.financial_concept_id) LIMIT 1",
                "ORIGIN_STATEMENT_CONCEPT_REFERENCE_MISSING",
            )
            scan.add("origin_companies", [root / "origin/outputs/dwd_listed_company.parquet"])
            scan.reject(
                "SELECT 1 FROM dwd_financial_report r WHERE NOT EXISTS "
                "(SELECT 1 FROM origin_companies c WHERE c.company_id=r.company_id) LIMIT 1",
                "ORIGIN_STATEMENT_COMPANY_REFERENCE_MISSING",
            )
        summaries = {
            t: semantic_scan([root / "outputs" / (t + ".parquet")], ss[t], check=budget.check)
            for t in TABLES
        }
        if summaries != plan["summaries"]:
            raise BoundedError("ORIGIN_STATEMENT_SUMMARY_CHANGED")
        return dict(
            status="PASS_FULL_LOCAL_ORIGIN_STATEMENT_REPLAY",
            rows=dict(counts),
            identity=plan["identity"],
            summaries=summaries,
            complete_Base=False,
            security_or_listing_extended=False,
            historical_PIT_qualified=False,
            source_network_or_old_roots_required=False,
            consumer_admission="NOT_GRANTED",
        )
