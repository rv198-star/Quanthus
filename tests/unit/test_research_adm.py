from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.research_adm import (
    ResearchADMError,
    collect_p4_historical_industry_supplement,
    load_p4_research_adm_policy,
)

ROOT = find_project_root()
POLICY = ROOT / "config/p4-research-adm.v1.json"
HIERARCHY = ROOT / "config/pipelines/csrc-industry-hierarchy.v1.json"


class _BaoStockResult:
    error_code = "0"
    error_msg = "success"

    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = iter(rows)
        self._current: list[str] | None = None

    def next(self) -> bool:
        try:
            self._current = next(self._rows)
        except StopIteration:
            self._current = None
            return False
        return True

    def get_row_data(self) -> list[str]:
        assert self._current is not None
        return list(self._current)


class _BaoStockFixture:
    __version__ = "0.9.3"

    def login(self):  # type: ignore[no-untyped-def]
        return type("Login", (), {"error_code": "0", "error_msg": "success"})()

    def logout(self) -> None:
        return None

    def query_stock_industry(self, *, date: str):  # type: ignore[no-untyped-def]
        if date == "2021-09-30":
            return _BaoStockResult(
                [
                    [
                        "2021-09-27",
                        "sz.000001",
                        "样本一",
                        "I65软件和信息技术服务业",
                        "证监会行业分类",
                    ],
                    ["2021-09-27", "sh.600001", "样本二", "", "证监会行业分类"],
                ]
            )
        if date == "2012-12-31":
            return _BaoStockResult(
                [
                    [
                        "2012-12-31",
                        "sh.600001",
                        "样本二",
                        "建筑业-土木工程建筑业",
                        "证监会行业分类",
                    ]
                ]
            )
        raise AssertionError(date)


def test_repository_p4_research_adm_contract_is_frozen() -> None:
    policy = load_p4_research_adm_policy(POLICY)

    assert policy.adm_id == "p4-event-research-adm"
    assert policy.adm_version == "1.1.0"
    assert policy.research_only is True
    assert policy.execution_authorized is False
    assert policy.assets == {
        "candidate_daily": "candidate_daily.parquet",
        "static_industry_mapping": "static_industry_mapping.json",
        "static_industry_source_receipt": "static_industry_source_receipt.json",
        "static_industry_coverage_audit": "static_industry_coverage_audit.json",
    }
    assert policy.historical_industry_reference.expected_security_count == 259
    assert policy.historical_industry_reference.provider_version == "0.9.3"
    assert policy.historical_industry_reference.source_content_digest.startswith("sha256:")
    assert "canonical_dwd_fact" in policy.forbidden_claims
    assert "operational_ads" in policy.forbidden_claims
    assert policy.semantic_digest == policy.computed_semantic_digest


def test_historical_industry_supplement_is_deterministic_and_checksum_bound() -> None:
    policy = load_p4_research_adm_policy(POLICY)
    provider = _BaoStockFixture()
    memberships, receipt = collect_p4_historical_industry_supplement(
        ["600001", "000001"],
        hierarchy_path=HIERARCHY,
        bs_module=provider,
    )

    assert memberships == {
        "000001": {
            "industry_code": "I65",
            "industry_name": "软件和信息技术服务业",
        },
        "600001": {
            "industry_code": "E48",
            "industry_name": "土木工程建筑业",
        },
    }
    assert [row["resolution"] for row in receipt["source_rows"]] == [
        "primary_csrc_2012_snapshot",
        "legacy_csrc_name_crosswalk_to_2012",
    ]

    binding = replace(
        policy.historical_industry_reference,
        expected_security_count=2,
        source_content_digest=str(receipt["content_digest"]),
    )
    repeated, repeated_receipt = collect_p4_historical_industry_supplement(
        ["000001", "600001"],
        hierarchy_path=HIERARCHY,
        binding=binding,
        bs_module=_BaoStockFixture(),
    )
    assert repeated == memberships
    assert repeated_receipt == receipt

    wrong = replace(binding, source_content_digest="sha256:" + "1" * 64)
    with pytest.raises(ResearchADMError, match="source digest differs"):
        collect_p4_historical_industry_supplement(
            ["000001", "600001"],
            hierarchy_path=HIERARCHY,
            binding=wrong,
            bs_module=_BaoStockFixture(),
        )


def test_p4_research_adm_rejects_authority_escalation(tmp_path: Path) -> None:
    raw = json.loads(POLICY.read_text(encoding="utf-8"))
    raw["execution_authorized"] = True
    path = tmp_path / "invalid-adm.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResearchADMError, match="cannot carry execution authority"):
        load_p4_research_adm_policy(path)
