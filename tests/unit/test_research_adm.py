from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.research_adm import ResearchADMError, load_p4_research_adm_policy

ROOT = find_project_root()
POLICY = ROOT / "config/p4-research-adm.v1.json"


def test_repository_p4_research_adm_contract_is_frozen() -> None:
    policy = load_p4_research_adm_policy(POLICY)

    assert policy.adm_id == "p4-event-research-adm"
    assert policy.adm_version == "1.0.0"
    assert policy.research_only is True
    assert policy.execution_authorized is False
    assert policy.assets == {
        "candidate_daily": "candidate_daily.parquet",
        "static_industry_mapping": "static_industry_mapping.json",
        "static_industry_coverage_audit": "static_industry_coverage_audit.json",
    }
    assert "canonical_dwd_fact" in policy.forbidden_claims
    assert "operational_ads" in policy.forbidden_claims
    assert policy.semantic_digest == policy.computed_semantic_digest


def test_p4_research_adm_rejects_authority_escalation(tmp_path: Path) -> None:
    raw = json.loads(POLICY.read_text(encoding="utf-8"))
    raw["execution_authorized"] = True
    path = tmp_path / "invalid-adm.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ResearchADMError, match="cannot carry execution authority"):
        load_p4_research_adm_policy(path)
