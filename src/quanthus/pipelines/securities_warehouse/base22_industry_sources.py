"""Verify recovered industry source lineage without admitting ADM or historical PIT.

The provider receipt contains selected original source fields, not retained wire
responses for the whole provider universe. Its static reference purpose is kept.
This evaluator emits hashes and dispositions, never DWD membership facts.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.p3_temporary_industry import encoded, qualify, sha
from quanthus.research_adm import _normalize_industry_name

from .base22 import digest
from .base22_sources import SourceAssessmentError

CONTRACT = "p10-base22-original-industry-lineage.v1"
PURPOSE = "static_non_pit_concentration_only"
EXCHANGE_MIC = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}
COUNTS = {"dwd_native": 5201, "p4_sealed_reference": 49, "temporary_static_source": 1}
INPUTS = {
    "frozen_identities",
    "historical_receipt",
    "static_receipt",
    "static_mapping",
    "temporary_receipt",
    "temporary_response",
    "hierarchy",
    "native_manifest",
    "listing",
    "security",
    "venue",
    "industry_node",
    "membership",
    "evaluator",
}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SourceAssessmentError(reason)


def _semantic(document: dict[str, Any], field: str) -> None:
    _require(
        document.get(field) == digest_value({k: v for k, v in document.items() if k != field}),
        "INDUSTRY_ORIGINAL_SEMANTIC_IDENTITY_MISMATCH",
    )


def historical_rows(
    receipt: dict[str, Any], hierarchy: dict[str, Any], hierarchy_sha256: str
) -> dict[str, dict[str, str]]:
    """Check the recorded raw industry field as well as the resolved hierarchy."""
    _semantic(receipt, "content_digest")
    _require(
        receipt.get("schema_version") == "quanthus.p4-adm-industry-source-receipt.v1"
        and receipt.get("hierarchy_sha256") == hierarchy_sha256
        and receipt.get("provider") == "baostock.query_stock_industry"
        and receipt.get("provider_version") == "0.9.3"
        and receipt.get("classification") == "证监会行业分类",
        "INDUSTRY_ORIGINAL_HEADER_INVALID",
    )
    leaves = hierarchy["leaf_nodes"]
    result: dict[str, dict[str, str]] = {}
    fields = {
        "security_code",
        "security_name",
        "industry_code",
        "industry_name",
        "provider_industry",
        "provider_update_date",
        "query_date",
        "resolution",
    }
    rows = receipt.get("source_rows")
    if not isinstance(rows, list):
        raise SourceAssessmentError("INDUSTRY_ORIGINAL_ROWS_INVALID")
    for row in rows:
        _require(
            isinstance(row, dict)
            and set(row) == fields
            and all(isinstance(v, str) for v in row.values()),
            "INDUSTRY_ORIGINAL_ROW_SCHEMA_INVALID",
        )
        code, leaf = row["security_code"], row["industry_code"]
        _require(
            re.fullmatch(r"\d{6}", code) is not None and code not in result,
            "INDUSTRY_ORIGINAL_DUPLICATE_OR_INVALID_CODE",
        )
        _require(leaves.get(leaf) == row["industry_name"], "INDUSTRY_HIERARCHY_MISMATCH")
        try:
            query = date.fromisoformat(row["query_date"])
            update = date.fromisoformat(row["provider_update_date"])
        except ValueError:
            raise SourceAssessmentError("INDUSTRY_ORIGINAL_DATE_INVALID") from None
        _require(update <= query, "INDUSTRY_ORIGINAL_FUTURE_UPDATE")
        if row["resolution"] == "primary_csrc_2012_snapshot":
            _require(row["query_date"] == receipt["primary_query_date"], "INDUSTRY_QUERY_MISMATCH")
            match = re.fullmatch(r"([A-Z]\d+)(.+)", row["provider_industry"])
            _require(
                match is not None and match.groups() == (leaf, row["industry_name"]),
                "INDUSTRY_RAW_RESOLUTION_MISMATCH",
            )
        else:
            _require(
                row["resolution"] == "legacy_csrc_name_crosswalk_to_2012"
                and row["query_date"] == receipt["legacy_query_date"],
                "INDUSTRY_QUERY_MISMATCH",
            )
            normalized = _normalize_industry_name(row["provider_industry"].split("-")[-1])
            matches = [k for k, v in leaves.items() if _normalize_industry_name(v) == normalized]
            _require(matches == [leaf], "INDUSTRY_RAW_RESOLUTION_MISMATCH")
        result[code] = row
    _require(
        receipt.get("selected_security_count") == len(result) and list(result) == sorted(result),
        "INDUSTRY_ORIGINAL_SET_MISMATCH",
    )
    return result


def exact_industry_lineage(
    *,
    identities: list[dict[str, Any]],
    historical: dict[str, Any],
    static_receipt: dict[str, Any],
    static_mapping: dict[str, Any],
    temporary: dict[str, Any],
    response: dict[str, Any],
    hierarchy: dict[str, Any],
    listings: list[dict[str, Any]],
    securities: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    input_closure: dict[str, str],
    observed_at: datetime,
) -> dict[str, Any]:
    """Bind the frozen 49+1 exceptions to source-date native identities offline.

    Callers must verify exact physical input hashes before and after reading. The
    complete closure is recorded here; restored paths confer no source priority.
    Original static qualification is not inherited as formal DWD repair authority.
    """
    _require(
        set(input_closure) == INPUTS
        and all(
            re.fullmatch(r"sha256:[0-9a-f]{64}", h) is not None for h in input_closure.values()
        ),
        "INDUSTRY_INPUT_CLOSURE_INVALID",
    )
    _require(observed_at.tzinfo is not None, "INDUSTRY_OBSERVATION_TIME_UNKNOWN")
    _require(
        Counter(r["source"] for r in identities) == COUNTS
        and len({r["security_id"] for r in identities}) == len(identities),
        "INDUSTRY_EXACT_FROZEN_SET_INVALID",
    )
    original_rows = historical_rows(historical, hierarchy, input_closure["hierarchy"])
    _semantic(static_receipt, "content_digest")
    _semantic(static_mapping, "artifact_digest")
    _require(
        static_receipt.get("schema_version") == "quanthus.p4-adm-static-industry-receipt.v1"
        and static_receipt["historical_supplement"] == historical
        and static_mapping["usage"] == PURPOSE
        and static_mapping["source"]["source_revision"] == static_receipt["content_digest"],
        "INDUSTRY_ADM_COMPARISON_LINK_INVALID",
    )
    _require(
        temporary.get("schema_version") == "quanthus.p3-temporary-industry-receipt.v1"
        and temporary.get("usage") == PURPOSE
        and temporary.get("formal_DWD_repair") is False
        and temporary.get("historical_as_known") is False
        and temporary.get("response_sha256") == input_closure["temporary_response"]
        and temporary.get("response_semantic_digest") == sha(encoded(response))
        and temporary["request"]["hierarchy_sha256"] == input_closure["hierarchy"],
        "INDUSTRY_TEMPORARY_SCOPE_OR_IDENTITY_INVALID",
    )
    mapped = qualify(response, {"source": temporary["request"]}, hierarchy)
    _require(mapped == temporary["qualified_mapping"], "INDUSTRY_TEMPORARY_MAPPING_MISMATCH")
    acquired = datetime.fromisoformat(temporary["retrieved_at"])
    _require(
        acquired.tzinfo is not None and acquired <= observed_at,
        "INDUSTRY_OBSERVATION_PRECEDES_ORIGINAL_ACQUISITION",
    )
    venue_mic = {v["venue_id"]: v["mic_code"] for v in venues}
    parent_ids = {n["node_code"]: n["industry_node_id"] for n in nodes if n["node_level"] == 1}
    covered = {r["company_id"] for r in memberships}
    _require(
        len(memberships) == 5201
        and len(covered) == 5201
        and all(r["company_id"] in covered for r in identities if r["source"] == "dwd_native"),
        "INDUSTRY_NATIVE_SNAPSHOT_COVERAGE_CHANGED",
    )
    by_security: dict[str, list[dict[str, Any]]] = {}
    for row in listings:
        by_security.setdefault(row["security_id"], []).append(row)
    native_security: dict[str, list[dict[str, Any]]] = {}
    for row in securities:
        native_security.setdefault(row["security_id"], []).append(row)
    comparison = {r["security_id"]: r for r in static_mapping["memberships"]}
    _require(
        len(comparison) == len(static_mapping["memberships"]),
        "INDUSTRY_ADM_COMPARISON_DUPLICATE",
    )
    evidence = []
    for identity in sorted(identities, key=lambda r: r["security_id"]):
        origin = identity["source"]
        if origin == "dwd_native":
            continue
        _require(identity["company_id"] not in covered, "INDUSTRY_EXCEPTION_ALREADY_NATIVE")
        _require(identity["venue_id"] in EXCHANGE_MIC, "INDUSTRY_SOURCE_MARKET_UNKNOWN")
        code = identity["listing_code"]
        if origin == "p4_sealed_reference":
            _require(code in original_rows, "INDUSTRY_SELECTED_SOURCE_MISSING")
            source_row = original_rows[code]
            day = source_row["query_date"]
            leaf = source_row["industry_code"]
            audit = comparison.get(identity["security_id"], {})
            _require(
                audit
                == {
                    "security_id": identity["security_id"],
                    "stock_code": code,
                    "exchange": identity["venue_id"],
                    "level1_industry_id": identity["level1_industry_id"],
                },
                "INDUSTRY_ADM_AUDIT_IDENTITY_MISMATCH",
            )
            original_acquired: str | None = None
            source_hash = input_closure["historical_receipt"]
        else:
            target = temporary["target"]
            _require(
                all(identity[k] == target[k] for k in ("security_id", "company_id"))
                and code == target["security_code"]
                and identity["venue_id"] == target["exchange"],
                "INDUSTRY_TEMPORARY_TARGET_MISMATCH",
            )
            day, leaf = temporary["request"]["query_date"], mapped["leaf_code"]
            source_row = response
            original_acquired = temporary["retrieved_at"]
            source_hash = input_closure["temporary_response"]
        _require(
            parent_ids.get(leaf[0]) == identity["level1_industry_id"],
            "INDUSTRY_SOURCE_PARENT_CONFLICT",
        )
        instant = datetime.combine(date.fromisoformat(day), time(), ZoneInfo("Asia/Shanghai"))
        eligible = [
            r
            for r in by_security.get(identity["security_id"], [])
            if r["listing_code"] == code
            and venue_mic.get(r["venue_id"]) == EXCHANGE_MIC.get(identity["venue_id"])
            and r["effective_from"] <= instant
            and (r["effective_to"] is None or instant < r["effective_to"])
        ]
        companies = [
            r
            for r in native_security.get(identity["security_id"], [])
            if r["company_id"] == identity["company_id"]
            and r["effective_from"] <= instant
            and (r["effective_to"] is None or instant < r["effective_to"])
        ]
        _require(
            len(eligible) == 1
            and len(companies) == 1
            and any(
                b["listing_id"] == eligible[0]["listing_id"]
                and b["listing_code"] == code
                and b["venue_id"] == identity["venue_id"]
                for b in identity["listing_bindings"]
            ),
            "INDUSTRY_SOURCE_DATE_NATIVE_IDENTITY_INVALID",
        )
        evidence.append(
            {
                "company_id": identity["company_id"],
                "security_id": identity["security_id"],
                "listing_id": eligible[0]["listing_id"],
                "source_kind": origin,
                "query_date": day,
                "original_acquisition": original_acquired,
                "source_file_sha256": source_hash,
                "source_row_sha256": digest_value(source_row),
                "native_listing_row_sha256": digest(eligible[0]),
                "knowledge_availability": "UNAVAILABLE_FOR_HISTORICAL_PIT",
                "disposition": "LINEAGE_PROVEN_STATIC_REFERENCE_ONLY",
            }
        )
    body = {
        "schema_version": CONTRACT,
        "input_closure": input_closure,
        "new_observed_at": observed_at.isoformat(),
        "source_counts": dict(COUNTS),
        "exact_exceptions": evidence,
        "exact_exception_set_sha256": digest(evidence),
        "original_static_lineage": "PASS",
        "historical_PIT": "NOT_GRANTED",
        "formal_DWD_membership_admission": "NOT_GRANTED",
        "positive_memberships_written": 0,
        "original_full_provider_wire_response_retained": False,
        "ADM_imported_as_DWD_fact": False,
        "frozen_consumer_policy_changed": False,
    }
    return {**body, "result_digest": digest(body)}
