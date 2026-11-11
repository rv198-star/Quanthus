"""Recovered static industry originals do not authorize historical DWD facts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.p3_temporary_industry import encoded, qualify, sha
from quanthus.pipelines.securities_warehouse.base22_industry_sources import (
    INPUTS,
    exact_industry_lineage,
    historical_rows,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def sealed(document: dict[str, Any], field: str) -> dict[str, Any]:
    body = {k: v for k, v in document.items() if k != field}
    return {**body, field: digest_value(body)}


@pytest.fixture
def fixture() -> dict[str, Any]:
    h = "sha256:" + "a" * 64
    hierarchy = {"leaf_nodes": {"C13": "测试工业"}, "parent_nodes": {"C": "测试制造业"}}
    ids = [
        {"source": "dwd_native", "security_id": f"native{i}", "company_id": f"native{i}"}
        for i in range(5201)
    ]
    original = []
    comparison = []
    listings = []
    securities = []
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        code = f"{i + 1:06d}" if i < 49 else "688287"
        security, listing, company = f"s{i}", f"l{i}", f"c{i}"
        ids.append(
            {
                "source": "p4_sealed_reference" if i < 49 else "temporary_static_source",
                "security_id": security,
                "company_id": company,
                "listing_code": code,
                "venue_id": "SSE",
                "level1_industry_id": "nodeC",
                "listing_bindings": [
                    {"listing_id": listing, "listing_code": code, "venue_id": "SSE"}
                ],
            }
        )
        listings.append(
            {
                "security_id": security,
                "listing_id": listing,
                "listing_code": code,
                "venue_id": "venueSSE",
                "effective_from": start,
                "effective_to": None,
            }
        )
        securities.append(
            {
                "security_id": security,
                "company_id": company,
                "effective_from": start,
                "effective_to": None,
            }
        )
        if i < 49:
            original.append(
                {
                    "security_code": code,
                    "security_name": "测试公司",
                    "industry_code": "C13",
                    "industry_name": "测试工业",
                    "provider_industry": "C13测试工业",
                    "provider_update_date": "2021-09-01",
                    "query_date": "2021-09-30",
                    "resolution": "primary_csrc_2012_snapshot",
                }
            )
            comparison.append(
                {
                    "security_id": security,
                    "stock_code": code,
                    "exchange": "SSE",
                    "level1_industry_id": "nodeC",
                }
            )
    historical = sealed(
        {
            "schema_version": "quanthus.p4-adm-industry-source-receipt.v1",
            "provider": "baostock.query_stock_industry",
            "provider_version": "0.9.3",
            "classification": "证监会行业分类",
            "hierarchy_sha256": h,
            "primary_query_date": "2021-09-30",
            "legacy_query_date": "2012-12-31",
            "selected_security_count": 49,
            "source_rows": original,
        },
        "content_digest",
    )
    outer = sealed(
        {
            "schema_version": "quanthus.p4-adm-static-industry-receipt.v1",
            "historical_supplement": historical,
        },
        "content_digest",
    )
    mapping = sealed(
        {
            "usage": "static_non_pit_concentration_only",
            "source": {"source_revision": outer["content_digest"]},
            "memberships": comparison,
        },
        "artifact_digest",
    )
    response = {
        "error_code": "0",
        "fields": ["updateDate", "code", "code_name", "industry", "industryClassification"],
        "rows": [["2024-10-01", "sh.688287", "测试公司", "C13测试工业", "证监会行业分类"]],
    }
    request = {
        "provider_code": "sh.688287",
        "classification": "证监会行业分类",
        "query_date": "2024-10-09",
        "hierarchy_sha256": h,
    }
    temporary = {
        "schema_version": "quanthus.p3-temporary-industry-receipt.v1",
        "usage": "static_non_pit_concentration_only",
        "formal_DWD_repair": False,
        "historical_as_known": False,
        "request": request,
        "response_semantic_digest": sha(encoded(response)),
        "response_sha256": sha(encoded(response) + b"\n"),
        "qualified_mapping": qualify(response, {"source": request}, hierarchy),
        "retrieved_at": "2026-10-02T17:55:56+00:00",
        "target": {
            "security_id": "s49",
            "company_id": "c49",
            "security_code": "688287",
            "exchange": "SSE",
        },
    }
    closure = dict.fromkeys(INPUTS, h)
    closure["temporary_response"] = temporary["response_sha256"]
    return {
        "identities": ids,
        "historical": historical,
        "static_receipt": outer,
        "static_mapping": mapping,
        "temporary": temporary,
        "response": response,
        "hierarchy": hierarchy,
        "listings": listings,
        "securities": securities,
        "venues": [{"venue_id": "venueSSE", "mic_code": "XSHG"}],
        "nodes": [{"node_level": 1, "node_code": "C", "industry_node_id": "nodeC"}],
        "memberships": [{"company_id": f"native{i}"} for i in range(5201)],
        "input_closure": closure,
        "observed_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
    }


def test_exact_50_lineage_preserves_static_purpose_and_real_clocks(fixture: dict[str, Any]) -> None:
    before = deepcopy(fixture)
    evidence = exact_industry_lineage(**fixture)
    assert len(evidence["exact_exceptions"]) == 50
    assert evidence["source_counts"] == {
        "dwd_native": 5201,
        "p4_sealed_reference": 49,
        "temporary_static_source": 1,
    }
    assert sum(e["original_acquisition"] is None for e in evidence["exact_exceptions"]) == 49
    assert evidence["historical_PIT"] == "NOT_GRANTED"
    assert evidence["formal_DWD_membership_admission"] == "NOT_GRANTED"
    assert evidence["positive_memberships_written"] == 0
    assert evidence["ADM_imported_as_DWD_fact"] is False
    assert fixture == before


@pytest.mark.parametrize(
    "change,reason",
    [
        ("unknown_source", "EXACT_FROZEN_SET"),
        ("lost_native", "NATIVE_SNAPSHOT_COVERAGE"),
        ("wrong_market", "SOURCE_DATE_NATIVE_IDENTITY"),
        ("after_query", "SOURCE_DATE_NATIVE_IDENTITY"),
        ("duplicate_listing", "SOURCE_DATE_NATIVE_IDENTITY"),
        ("company_not_effective", "SOURCE_DATE_NATIVE_IDENTITY"),
        ("native_role_escalation", "TEMPORARY_SCOPE_OR_IDENTITY"),
        ("missing_closure", "INPUT_CLOSURE"),
        ("fake_earlier_observation", "OBSERVATION_PRECEDES"),
    ],
)
def test_source_and_native_binding_fail_closed(
    fixture: dict[str, Any], change: str, reason: str
) -> None:
    if change == "unknown_source":
        fixture["identities"][-1]["source"] = "unknown"
    elif change == "lost_native":
        fixture["memberships"].pop()
    elif change == "wrong_market":
        fixture["venues"][0]["mic_code"] = "XSHE"
    elif change == "after_query":
        fixture["listings"][0]["effective_from"] = datetime(2022, 1, 1, tzinfo=timezone.utc)
    elif change == "duplicate_listing":
        fixture["listings"].append(deepcopy(fixture["listings"][0]))
    elif change == "company_not_effective":
        fixture["securities"][0]["effective_to"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    elif change == "native_role_escalation":
        fixture["temporary"]["formal_DWD_repair"] = True
    elif change == "missing_closure":
        del fixture["input_closure"]["native_manifest"]
    elif change == "fake_earlier_observation":
        fixture["observed_at"] = datetime(2026, 8, 31, tzinfo=timezone.utc)
    with pytest.raises(SourceAssessmentError, match=reason):
        exact_industry_lineage(**fixture)


def test_resigned_original_raw_crosswalk_mismatch_rejected(fixture: dict[str, Any]) -> None:
    receipt = deepcopy(fixture["historical"])
    receipt["source_rows"][0]["provider_industry"] = "C14不匹配"
    receipt = sealed(receipt, "content_digest")
    with pytest.raises(SourceAssessmentError, match="RAW_RESOLUTION_MISMATCH"):
        historical_rows(receipt, fixture["hierarchy"], fixture["input_closure"]["hierarchy"])


def test_original_legacy_name_crosswalk_offline(fixture: dict[str, Any]) -> None:
    receipt = deepcopy(fixture["historical"])
    row = receipt["source_rows"][0]
    row.update(
        provider_industry="旧分类-测试工业",
        provider_update_date="2012-01-01",
        query_date="2012-12-31",
        resolution="legacy_csrc_name_crosswalk_to_2012",
    )
    receipt = sealed(receipt, "content_digest")
    assert (
        historical_rows(receipt, fixture["hierarchy"], fixture["input_closure"]["hierarchy"])[
            row["security_code"]
        ]
        == row
    )


def test_nonselected_legacy_omission_does_not_qualify_whole_receipt(
    fixture: dict[str, Any],
) -> None:
    """The recovered receipt may retain extra rows outside the frozen 49."""
    extra = deepcopy(fixture["historical"]["source_rows"][0])
    extra.update(
        security_code="999999",
        provider_industry="",
        provider_update_date="2012-01-01",
        query_date="2012-12-31",
        resolution="legacy_csrc_name_crosswalk_to_2012",
    )
    fixture["historical"]["source_rows"].append(extra)
    fixture["historical"]["selected_security_count"] = 50
    fixture["historical"] = sealed(fixture["historical"], "content_digest")
    fixture["static_receipt"]["historical_supplement"] = fixture["historical"]
    fixture["static_receipt"] = sealed(fixture["static_receipt"], "content_digest")
    fixture["static_mapping"]["source"]["source_revision"] = fixture["static_receipt"][
        "content_digest"
    ]
    fixture["static_mapping"] = sealed(fixture["static_mapping"], "artifact_digest")
    result = exact_industry_lineage(**fixture)
    assert result["retained_historical_receipt_rows"] == 50
    assert result["qualified_selected_historical_rows"] == 49
    assert result["outside_frozen_exception_scope_rows_NOT_QUALIFIED"] == 1
    assert result["formal_DWD_membership_admission"] == "NOT_GRANTED"


def test_missing_selected_source_still_blocks(fixture: dict[str, Any]) -> None:
    with pytest.raises(SourceAssessmentError, match="SELECTED_SOURCE_MISSING"):
        historical_rows(
            fixture["historical"],
            fixture["hierarchy"],
            fixture["input_closure"]["hierarchy"],
            selected_codes={"999999"},
        )


def test_selected_legacy_original_field_omission_still_blocks(fixture: dict[str, Any]) -> None:
    receipt = deepcopy(fixture["historical"])
    row = receipt["source_rows"][0]
    row.update(
        provider_industry="",
        provider_update_date="2012-01-01",
        query_date="2012-12-31",
        resolution="legacy_csrc_name_crosswalk_to_2012",
    )
    receipt = sealed(receipt, "content_digest")
    with pytest.raises(SourceAssessmentError, match="RAW_RESOLUTION_MISMATCH"):
        historical_rows(
            receipt,
            fixture["hierarchy"],
            fixture["input_closure"]["hierarchy"],
            selected_codes={row["security_code"]},
        )
