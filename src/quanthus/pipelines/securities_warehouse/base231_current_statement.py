"""New current-knowledge statements through September, with genuine capture dates.

A fresh envelope does not restore the old weekly snapshot, qualify old unknown
currency literals, or establish historical provider PIT. One decoded dependency
supplies reports, concepts and facts; the retained September contract is unchanged.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import MAX_FACTS, MAX_RAW_BYTES, TABLES, _clock
from .base22_precision import decimal_profile
from .base23_financial_reconfirmation import FIELDS
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialStatementSource,
    _expand_decoded_financial_envelope,
    decode_financial_envelope,
    financial_reporting_schemas,
    iter_statement_reports,
)

CONTRACT_ID = "p10-base231-new-current-complete-statement-observations-v1"
THROUGH = date(2026, 9, 30)


def current_statement(
    raw: dict[str, Any],
    *,
    source: FinancialStatementSource,
    resolver: FinancialCompanyResolver,
    observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    if (
        set(raw) != FIELDS
        or raw.get("source_revision") != CONTRACT_ID
        or raw.get("source") != "sina.CompanyFinanceService.getFinanceReport2022"
        or not re.fullmatch(r"[036][0-9]{5}", str(raw.get("stock_code", "")))
        or raw.get("security_id") != raw.get("stock_code")
        or raw.get("statement_type")
        != {"fzb": "balance_sheet", "lrb": "income_statement", "llb": "cash_flow"}.get(
            str(raw.get("provider_source", ""))
        )
        or type(raw.get("period_count")) is not int
        or type(raw.get("requested_periods")) is not int
        or not 0 <= raw["period_count"] <= raw["requested_periods"] <= 128
    ):
        raise BoundedError("CURRENT_STATEMENT_EXACT_SOURCE_WRAPPER_REQUIRED")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if (
        acquired > observed
        or raw["market_date"] != acquired.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    ):
        raise BoundedError("CURRENT_STATEMENT_ACTUAL_CAPTURE_CLOCK_CONFLICT")
    envelope = decode_financial_envelope(raw, max_raw_bytes=MAX_RAW_BYTES)
    periods = list(iter_statement_reports(envelope))
    if (
        len(periods) != raw["period_count"]
        or sum(len(body["data"]) for _, body, _ in periods) > MAX_FACTS
    ):
        raise BoundedError("CURRENT_STATEMENT_COMPLETE_PERIOD_OR_ITEM_CENSUS_CHANGED")
    after = 0
    for period, body, _ in periods:
        period_date = datetime.strptime(period, "%Y%m%d").date()
        if period_date > THROUGH:
            after += 1
            continue
        if not re.fullmatch(r"[A-Z]{3}", str(body.get("rCurrency", ""))):
            raise BoundedError("CURRENT_STATEMENT_EXPLICIT_REPORT_CURRENCY_REQUIRED")
        for item in body["data"]:
            if item.get("item_display_type") != 1 and not (
                item.get("item_value") is None or isinstance(item["item_value"], str)
            ):
                raise BoundedError("CURRENT_STATEMENT_ORIGINAL_SCALAR_TOKEN_UNAVAILABLE")
    expanded = _expand_decoded_financial_envelope(
        raw, envelope=envelope, source=source, company_resolver=resolver, through_date=THROUGH
    )
    rows = dict(
        zip(
            TABLES,
            [list(expanded.reports), list(expanded.concepts), list(expanded.facts)],
            strict=True,
        )
    )
    counts: Counter[str] = Counter()
    for table, values in rows.items():
        for row in values:
            if row["knowledge_from"] > observed:
                raise BoundedError("CURRENT_STATEMENT_PARENT_NOT_KNOWN")
            row["knowledge_from"] = observed
            if table == TABLES[1]:
                continue
            body = envelope.report_list[row["period_end"].strftime("%Y%m%d")]
            publication = body.get("publish_date")
            if publication in {None, ""}:
                published = None
                counts["PUBLICATION_UNKNOWN"] += 1
            elif isinstance(publication, str) and re.fullmatch(
                r"[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}", publication
            ):
                # A declared calendar date cannot establish a time instant.
                # Keep its literal in the complete local raw envelope.
                publication_day = datetime.strptime(publication.replace("-", ""), "%Y%m%d").date()
                if publication_day > acquired.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    raise BoundedError("CURRENT_STATEMENT_DECLARED_PUBLICATION_AFTER_CAPTURE")
                published = None
                counts["PUBLICATION_DATE_ONLY_INSTANT_UNKNOWN"] += 1
            else:
                published = _clock(publication)
                counts["PUBLICATION_EXPLICIT_INSTANT"] += 1
            if published is not None and published > acquired:
                raise BoundedError("CURRENT_STATEMENT_PUBLICATION_AFTER_REAL_CAPTURE")
            row["published_at"] = published
            if table == TABLES[2]:
                index = int(row["source_record_id"].rsplit("&item_index=", 1)[1])
                token = body["data"][index].get("item_value")
                profile = decimal_profile(token, precision=38, scale=10)
                counts[profile["classification"]] += 1
                row["value_decimal"] = Decimal(token) if profile["fits"] else None
                row["value_text"] = (
                    token if not profile["fits"] and profile["classification"] != "NULL" else None
                )
                row["is_nil"] = profile["classification"] == "NULL"
    ss = financial_reporting_schemas()
    for table, values in rows.items():
        native = pa.Table.from_pylist(values, schema=ss[table])
        if native.to_pylist() != values or any(
            not f.nullable and native.column(f.name).null_count for f in ss[table]
        ):
            raise BoundedError("CURRENT_STATEMENT_NATIVE_VALUE_OR_CLOCK_CHANGED")
        rows[table] = native.to_pylist()
    proof = dict(
        contract_id=CONTRACT_ID,
        source_member_sha256=source.source_sha256,
        source_revision=source.source_revision,
        raw_payload_sha256=raw["raw_payload_sha256"],
        complete_envelope_fingerprint=digest(
            {k: v for k, v in raw.items() if k != "raw_payload_gzip"}
        ),
        original_acquired_at=raw["acquired_at"],
        actual_capture_date=raw["market_date"],
        acquired_at=acquired,
        governance_observed_at=observed,
        decoded_dependencies_built=1,
        after_through_reports_held=after,
        currency_authority="EXPLICIT_ORIGINAL_REPORT_rCurrency",
        row_counts={t: len(v) for t, v in rows.items()},
        typed_rows_sha256={t: digest(v) for t, v in rows.items()},
        scalar_classifications=dict(counts),
        quality_exceptions=list(expanded.quality_exceptions),
        current_capture_not_September=acquired.astimezone(ZoneInfo("Asia/Shanghai")).date()
        > THROUGH,
        old_F10_currency_inferred=False,
        historical_PIT_qualified=False,
        old_values_overwritten=False,
        complete_Base=False,
        consumer_admission="NOT_GRANTED",
    )
    return rows, proof


class ConceptAccumulator:
    """A bounded key census for this registered <=1,048,576-atom source batch."""

    def __init__(self) -> None:
        self.count = 0
        self.groups: dict[str, dict[str, Any]] = {}

    def add(self, rows: list[dict[str, Any]]) -> None:
        meta = {"record_source", "source_record_id", "source_revision", "source_artifact_sha256"}
        self.count += len(rows)
        if self.count > 1_048_576:
            raise BoundedError("CURRENT_STATEMENT_CONCEPT_CENSUS_BOUND")
        for row in rows:
            key = digest(
                [row[k] for k in ("financial_concept_id", "effective_from", "knowledge_from")]
            )
            business = digest({k: v for k, v in row.items() if k not in meta})
            sha = digest(row)
            group = self.groups.get(key)
            if group is None:
                self.groups[key] = dict(
                    business=business, representative=row, representative_sha=sha, sources={sha}
                )
                continue
            if business != group["business"]:
                raise BoundedError("CURRENT_STATEMENT_CONCEPT_BUSINESS_CONFLICT")
            group["sources"].add(sha)
            if sha < group["representative_sha"]:
                group["representative"], group["representative_sha"] = row, sha

    def finish(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        chosen, proof = [], []
        for key in sorted(self.groups):
            group = self.groups[key]
            chosen.append(group["representative"])
            proof.append(
                dict(
                    version_key_sha256=key,
                    business_sha256=group["business"],
                    representative_sha256=group["representative_sha"],
                    source_rows_sha256=sorted(group["sources"]),
                )
            )
        return chosen, dict(
            concept_rows=self.count,
            output_concept_versions=len(chosen),
            records=proof,
            metadata_only_representative=True,
            financial_value_priority_inferred=False,
        )


def merge_equal_concepts(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Share equal business versions; choose metadata only and retain every source."""
    accumulator = ConceptAccumulator()
    accumulator.add(rows)
    return accumulator.finish()


def wrap_current_envelope(body: bytes, receipt: dict[str, Any]) -> dict[str, Any]:
    """Lossless new wrapper; no pretend historical accepted-child identity."""
    import gzip
    import hashlib
    import json
    from urllib.parse import parse_qs, urlsplit

    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= 2 * 1024**2
        or receipt.get("http_status") != "200"
        or receipt.get("curl_exit") != 0
        or receipt.get("body_sha256") != "sha256:" + hashlib.sha256(body).hexdigest()
    ):
        raise BoundedError("CURRENT_STATEMENT_REAL_RESPONSE_RECEIPT_REQUIRED")
    url = urlsplit(receipt["URI"])
    query = parse_qs(url.query)
    code, source = receipt["code"], receipt["provider_source"]
    prefix = "sh" if str(code).startswith("6") else "sz"
    if (
        url.scheme != "https"
        or url.hostname != "quotes.sina.cn"
        or url.path != "/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
        or query
        != {
            "paperCode": [prefix + code],
            "source": [source],
            "type": ["0"],
            "page": ["1"],
            "num": ["12"],
        }
    ):
        raise BoundedError("CURRENT_STATEMENT_EXACT_REQUEST_COORDINATE_REQUIRED")
    data = json.loads(body)
    if str(data.get("result", {}).get("status", {}).get("code")) != "0":
        raise BoundedError("CURRENT_STATEMENT_PROVIDER_RESPONSE_NOT_SUCCESS")
    reports = data.get("result", {}).get("data", {}).get("report_list")
    if not isinstance(reports, dict) or len(reports) > 12:
        raise BoundedError("CURRENT_STATEMENT_COMPLETE_REPORT_LIST_REQUIRED")
    periods = sorted(reports)
    latest = reports[periods[-1]] if periods else {}
    clock = _clock(receipt["completed_at"])
    return dict(
        security_id=code,
        stock_code=code,
        statement_type={"fzb": "balance_sheet", "lrb": "income_statement", "llb": "cash_flow"}[
            source
        ],
        provider_source=source,
        requested_periods=12,
        period_count=len(periods),
        earliest_report_date=periods[0] if periods else None,
        latest_report_date=periods[-1] if periods else None,
        latest_publish_date=str(latest.get("publish_date") or "") or None,
        latest_update_time=str(latest.get("update_time") or "") or None,
        raw_payload_gzip=repr(gzip.compress(body, mtime=0)).encode("ascii"),
        raw_payload_sha256=receipt["body_sha256"],
        raw_payload_bytes=len(body),
        market_date=clock.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        source="sina.CompanyFinanceService.getFinanceReport2022",
        source_revision=CONTRACT_ID,
        acquired_at=receipt["completed_at"],
    )


def iter_current_domain(root: Any, plan: dict[str, Any]) -> Any:
    """One source/parent closure and one decoded prerequisite per envelope."""
    import json
    from pathlib import Path

    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import file_sha
    from .base231_official_profile import QUALIFIED_PARENT
    from .financial_reporting import financial_company_resolver_from_listed_company_rows

    root = Path(root)
    parent_path = root / "inputs/qualified-parent-manifest.json"
    if file_sha(parent_path) != QUALIFIED_PARENT:
        raise BoundedError("CURRENT_STATEMENT_RETAINED_PARENT_IDENTITY_CHANGED")
    parent = json.loads(parent_path.read_bytes())
    parent_members = {r["path"]: r for r in parent["members"]}
    rows: dict[str, list[dict[str, Any]]] = {}
    for table, specs in plan["parents"].items():
        rows[table] = []
        for spec in specs:
            p = root / spec["local"]
            if file_sha(p) != parent_members["domain/" + spec["original"]]["sha256"]:
                raise BoundedError("CURRENT_STATEMENT_RETAINED_NATIVE_PARENT_CHANGED")
            rows[table].extend(pq.ParquetFile(p).read().to_pylist())
    if set(rows) != {"dwd_security", "dwd_security_identifier"}:
        raise BoundedError("CURRENT_STATEMENT_EXACT_PARENT_ROLES_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        rows["dwd_security"], rows["dwd_security_identifier"]
    )
    registration = json.loads((root / "inputs/collection-registration.json").read_bytes())
    execution = json.loads((root / "inputs/collection-execution.json").read_bytes())
    if execution["registration_sha256"] != file_sha(root / "inputs/collection-registration.json"):
        raise BoundedError("CURRENT_STATEMENT_COLLECTION_IDENTITY_CHANGED")
    targets = {r["label"]: r for r in registration["targets"]}
    receipts = {r["label"]: r for r in execution["receipts"]}
    selected = plan["selection"]
    if (
        len(selected) != len(targets)
        or len({r["label"] for r in selected}) != len(targets)
        or {r["label"] for r in selected} != set(targets)
    ):
        raise BoundedError("CURRENT_STATEMENT_EXACT_SELECTED_SOURCE_CENSUS_CHANGED")
    now = datetime.fromisoformat(plan["observed_at"])
    for spec in selected:
        label = spec["label"]
        receipt = json.loads((root / spec["receipt"]).read_bytes())
        if receipt != receipts[label] or any(
            receipt[k] != targets[label][k] for k in ("code", "provider_source", "URI")
        ):
            raise BoundedError("CURRENT_STATEMENT_COMPLETE_REGISTERED_RECEIPT_CHANGED")
        if receipt["status"] != "FULL_CURRENT_ENVELOPE_PENDING_QUALIFICATION":
            yield spec, None, dict(status="SOURCE_HELD", reason=receipt["status"])
            continue
        raw = pq.ParquetFile(root / spec["wrapper"]).read().to_pylist()
        wrapped = wrap_current_envelope((root / spec["body"]).read_bytes(), receipt)
        if len(raw) != 1 or raw[0] != wrapped:
            raise BoundedError("CURRENT_STATEMENT_LOSSLESS_RAW_WRAPPER_CHANGED")
        source = FinancialStatementSource(
            "statements",
            root / spec["wrapper"],
            spec["wrapper"],
            file_sha(root / spec["wrapper"]),
            CONTRACT_ID,
            "parent",
        )
        try:
            output, evidence = current_statement(
                wrapped, source=source, resolver=resolver, observed_at=now
            )
        except (RuntimeError, ValueError) as error:
            yield spec, None, dict(status="DOMAIN_HELD", reason=str(error))
            continue
        yield spec, output, evidence


def replay_current_domain(root: Any, scratch: Any) -> dict[str, Any]:
    """Full native/hash/source reconstruction with bounded temporary comparison."""
    import json
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from .base22 import canonical, file_sha, load_contract
    from .base22_bounded import Budget, GlobalScan, Limits, semantic_scan
    from .base22_sources import _tree, physical_path

    root, scratch = Path(root), Path(scratch)
    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("historical_PIT_qualified") is not False
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("CURRENT_STATEMENT_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("CURRENT_STATEMENT_PHYSICAL_SET_CHANGED")
    for name, spec in plan["members"].items():
        p = physical_path(root / name)
        if (
            root.resolve() not in p.parents
            or p.stat().st_nlink != 1
            or p.stat().st_size != spec["bytes"]
            or file_sha(p) != spec["sha256"]
        ):
            raise BoundedError("CURRENT_STATEMENT_LOCAL_MEMBER_CHANGED")
    for path, sha in plan["code_inputs"].items():
        if file_sha(Path(path)) != sha:
            raise BoundedError("CURRENT_STATEMENT_ORDINARY_CODE_CHANGED")
    ss = financial_reporting_schemas()
    if sorted(p.stem for p in (root / "outputs").glob("*.parquet")) != sorted(TABLES):
        raise BoundedError("CURRENT_STATEMENT_EXACT_OUTPUT_TABLES_CHANGED")
    concept_census = ConceptAccumulator()
    proof_hash = __import__("hashlib").sha256()
    counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    limits = Limits(
        free_floor_bytes=8 * 1024**3,
        allocated_bytes=1024**3,
        temp_bytes=512 * 1024**2,
        wall_seconds=3600,
    )
    with Budget(scratch, [root], limits) as budget:
        writers = {
            t: pq.ParquetWriter(scratch / (t + ".parquet"), ss[t], compression="zstd")
            for t in TABLES
            if t != "dwd_financial_concept"
        }
        try:
            for spec, output, evidence in iter_current_domain(root, plan):
                budget.check()
                proof_hash.update(canonical(dict(label=spec["label"], evidence=evidence)) + b"\n")
                if output is None:
                    statuses[evidence["status"] + ":" + evidence["reason"]] += 1
                    continue
                statuses["QUALIFIED_CURRENT_ENVELOPE"] += 1
                class_counts.update(evidence["scalar_classifications"])
                concept_census.add(output["dwd_financial_concept"])
                for table, writer in writers.items():
                    values = pa.Table.from_pylist(output[table], schema=ss[table])
                    writer.write_table(values)
                    counts[table] += values.num_rows
        finally:
            for writer in writers.values():
                writer.close()
        concepts, concept_proof = concept_census.finish()
        pq.write_table(
            pa.Table.from_pylist(concepts, schema=ss["dwd_financial_concept"]),
            scratch / "dwd_financial_concept.parquet",
            compression="zstd",
        )
        counts["dwd_financial_concept"] = len(concepts)
        if (
            dict(counts) != plan["rows"]
            or dict(statuses) != plan["statuses"]
            or dict(class_counts) != plan["scalar_classifications"]
            or "sha256:" + proof_hash.hexdigest() != plan["proof_stream_sha256"]
            or digest(concept_proof) != plan["concept_proof_sha256"]
        ):
            raise BoundedError("CURRENT_STATEMENT_FULL_RECONSTRUCTION_CENSUS_CHANGED")
        contract, _, _ = load_contract(root / "config")
        with GlobalScan(budget, read_only_roots=[root]) as scan:
            for table in TABLES:
                expected, actual = (
                    scratch / (table + ".parquet"),
                    root / "outputs" / (table + ".parquet"),
                )
                if pq.ParquetFile(actual).schema_arrow != ss[table]:
                    raise BoundedError("CURRENT_STATEMENT_NATIVE_SCHEMA_CHANGED")
                scan.add("e_" + table, [expected])
                scan.add(table, [actual])
                for left, right in [("e_" + table, table), (table, "e_" + table)]:
                    scan.reject(
                        "SELECT 1 FROM (SELECT * FROM "
                        + left
                        + " EXCEPT ALL SELECT * FROM "
                        + right
                        + ") LIMIT 1",
                        "CURRENT_STATEMENT_SERIALIZED_ROWS_CHANGED",
                    )
                keys = contract["tables"][table]["version_key"]
                scan.reject(
                    "SELECT 1 FROM "
                    + table
                    + " GROUP BY "
                    + ",".join(keys)
                    + " HAVING count(*)>1 LIMIT 1",
                    "CURRENT_STATEMENT_DUPLICATE_NATIVE_VERSION",
                )
            scan.reject(
                "SELECT 1 FROM dwd_financial_fact f LEFT JOIN dwd_financial_report r "
                "USING(financial_report_id) WHERE r.financial_report_id IS NULL LIMIT 1",
                "CURRENT_STATEMENT_REPORT_REFERENCE_MISSING",
            )
            scan.reject(
                "SELECT 1 FROM dwd_financial_fact f WHERE NOT EXISTS (SELECT 1 FROM "
                "dwd_financial_concept c WHERE "
                "c.financial_concept_id=f.financial_concept_id) LIMIT 1",
                "CURRENT_STATEMENT_CONCEPT_REFERENCE_MISSING",
            )
            scan.reject(
                "SELECT 1 FROM dwd_financial_report WHERE period_end>DATE '2026-09-30' LIMIT 1",
                "CURRENT_STATEMENT_BUSINESS_AFTER_THROUGH",
            )
        summaries = {
            t: semantic_scan([root / "outputs" / (t + ".parquet")], ss[t], check=budget.check)
            for t in TABLES
        }
        if summaries != plan["summaries"]:
            raise BoundedError("CURRENT_STATEMENT_RECOMPUTED_SUMMARY_CHANGED")
        return dict(
            status="PASS_FULL_LOCAL_CURRENT_STATEMENT_NATIVE_REPLAY",
            rows=dict(counts),
            input_identity=plan["identity"],
            summaries=summaries,
            source_network_or_old_roots_required=False,
            complete_Base=False,
            old_values_overwritten=False,
            historical_PIT_qualified=False,
            consumer_admission="NOT_GRANTED",
        )
