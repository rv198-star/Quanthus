from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R11 = ROOT / "config" / "research" / "r11"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_selected_contract_and_implementation_policy_are_cross_bound() -> None:
    current = json.loads((R11 / "CURRENT.json").read_text(encoding="utf-8"))
    contract_path = R11 / current["contract"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    implementation = json.loads((R11 / "implementation-policy.v1.json").read_text(encoding="utf-8"))

    assert current["raw_sha256"] == sha(contract_path)
    assert current["semantic_digest"] == contract["semantic_digest"]
    assert implementation["contract_semantic_digest"] == contract["semantic_digest"]
    assert implementation["contract"] == current["contract"]
    assert implementation["version"] == "1.2.1"
    assert contract["risk_policy"]["version"] == "1.0.1"
    assert implementation["risk"]["version"] == "1.0.2"
    assert implementation["risk"]["cash_basis"] == "canonical_cash_before_internal_holds"
    assert implementation["risk"]["pending_cash_contract"] == (
        "continuation-001/pending-cash-contract.v1.json"
    )
    assert implementation["quality_repair"]["findings"] == [
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6",
        "R7",
        "R8",
        "R9",
        "R10",
    ]


def test_frozen_authority_and_daily_adoption_stay_closed() -> None:
    contract = json.loads((R11 / "research-contract.v1.0.1.json").read_text(encoding="utf-8"))
    policy = json.loads((R11 / "implementation-policy.v1.json").read_text(encoding="utf-8"))
    coverage = json.loads((R11 / "adoption-coverage.json").read_text(encoding="utf-8"))
    ds_usage = json.loads((R11 / "ds-adoption-usage.v1.json").read_text(encoding="utf-8"))
    repairs = json.loads((R11 / "repair-dispositions.v1.json").read_text(encoding="utf-8"))

    assert contract["current_authority"] == {
        "design": True,
        "local_synthetic_code_and_tests": True,
        "new_market_row_read": False,
        "reference_calls": 0,
        "training": 0,
        "historical_capital_replay": False,
        "daily_serving": False,
        "production": False,
    }
    assert policy["authority"]["synthetic_only"] is True
    assert policy["authority"]["daily_adopted"] is False
    assert policy["risk"]["real_account_limits"] is None
    assert all(binding["status"] != "daily_adopted" for binding in coverage["bindings"])
    assert coverage["bindings"][1]["missing_fields"][0].endswith(
        "semantic_decision_authority=inconclusive"
    )
    assert ds_usage["mode"] == "non_authorizing_synthetic_preparation"
    assert ds_usage["daily_adopted"] is False
    assert ds_usage["bindings"]["A02"]["semantic_decision_authority"] == "inconclusive"
    assert ds_usage["bindings"]["A03"]["per_decision_publication_proven"] is False
    assert ds_usage["bindings"]["A04"]["real_account_limits"] is None
    assert all(
        any(item["scope"] == "raw_file" for item in binding.get("scope_digests", []))
        for binding in ds_usage["bindings"].values()
    )
    assert repairs["daily_adopted"] is False
    assert [finding["id"] for finding in repairs["findings"]] == [
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6",
        "R7",
        "R8",
        "R9",
        "R10",
    ]
    assert all(finding["status"] == "fixed" for finding in repairs["findings"])


def test_independent_goldens_are_immutable_and_cover_all_required_ids() -> None:
    golden_path = R11 / "lead-golden-cases.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert sha(golden_path) == "346dfb47ba9d3590a5cc1e9745f43c670f632c770561f4dbdb1c69a341694710"
    assert len(golden["cases"]) == 21
    assert {case["id"] for case in golden["cases"]} == {
        "F01",
        "F02",
        "F03",
        "F04",
        "F05",
        "F06",
        "F07",
        "F08",
        "F09",
        "Q01",
        "Q02",
        "Q03",
        "Q04",
        "Q05",
        "C01",
        "C02",
        "C03",
        "C04",
        "C05",
        "C06",
        "Q06",
    }
