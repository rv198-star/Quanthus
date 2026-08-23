from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quanthus.expert_evaluation.product_view import (
    ProductExpertEvidence,
    ProductExpertEvidenceError,
)
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.policy import find_project_root

ROOT = find_project_root()
CONFIG = ROOT / "config" / "product-expert-evidence.v1.json"
PRODUCTION_ARTIFACT_ROOT = Path(
    os.environ.get("QUANTHUS_TEST_EXPERT_ARTIFACT_ROOT", "/nonexistent")
)


def _view(config: Path = CONFIG, artifact_root: Path | None = None) -> ProductExpertEvidence:
    return ProductExpertEvidence(config, ROOT, artifact_root)


def test_unbound_and_unpublished_candidate_states_are_explicit(tmp_path: Path) -> None:
    assert _view().candidate("baseline.ma-trend", "2.0.0")["state"] == "not_applicable"
    assert _view().candidate("candidate.p4-trend-risk", "1.0.1") == {
        "state": "not_available",
        "strategy_binding": None,
        "experts": [],
        "assessment_scorecards": [],
        "economic_effects": [],
        "disagreement_cases": [],
        "limitations": [],
        "lifecycle_decision": None,
        "source_digests": {},
        "reason_codes": ["expert_artifact_root_unavailable"],
    }

    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["bindings"][0].update(
        package_id=None,
        package_digest=None,
        missing_reason_codes=["formal_attribution_not_published"],
    )
    path = tmp_path / "unavailable.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    unavailable = _view(path, tmp_path).candidate("candidate.p4-trend-risk", "1.0.1")
    assert unavailable["state"] == "not_available"
    assert unavailable["reason_codes"] == ["formal_attribution_not_published"]


@pytest.mark.parametrize("tamper", ["binding", "package"])
def test_exact_package_binding_fails_closed(tmp_path: Path, tamper: str) -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    if tamper == "binding":
        payload["bindings"][0]["package_digest"] = "sha256:" + "0" * 64
        reason = "expert_evidence_binding_drift"
    else:
        payload["packages"][0]["candidate"]["strategy_version"] = "1.0.2"
        reason = "expert_evidence_package_digest_mismatch"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductExpertEvidenceError, match=reason):
        _view(path, tmp_path).candidate("candidate.p4-trend-risk", "1.0.1")


def test_cross_candidate_package_is_rejected_before_artifact_read(tmp_path: Path) -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    package = payload["packages"][0]
    package["candidate"]["strategy_id"] = "candidate.foreign"
    digest_payload = dict(package)
    digest_payload.pop("package_digest")
    package["package_digest"] = digest_value(digest_payload)
    payload["bindings"][0].update(
        strategy_id="candidate.foreign",
        package_digest=package["package_digest"],
    )
    path = tmp_path / "foreign.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductExpertEvidenceError, match="expert_evidence_strategy_mismatch"):
        _view(path, tmp_path).candidate("candidate.foreign", "1.0.1")


@pytest.mark.skipif(
    not PRODUCTION_ARTIFACT_ROOT.is_dir(),
    reason="production expert artifact root is not mounted",
)
def test_production_projection_is_digest_resolved_and_authority_free() -> None:
    result = _view(artifact_root=PRODUCTION_ARTIFACT_ROOT).candidate(
        "candidate.p4-trend-risk", "1.0.1"
    )
    assert result["state"] == "available"
    assert [item["expert_id"] for item in result["experts"]] == [
        "expert.trend-stability",
        "expert.position-risk-review",
    ]
    assert len(result["assessment_scorecards"]) == 2
    assert len(result["disagreement_cases"]) == 2
    assert len(result["economic_effects"]) == 4
    assert result["lifecycle_decision"] is None
    assert result["source_digests"]["attribution_release"] == (
        "sha256:1754a4a3a185420734e6addab71110ee82ecd71fb4fbd182f9ee1506c49c98ff"
    )
    rendered = json.dumps(result, sort_keys=True)
    assert "/data/" not in rendered
    assert "execution_authorized" not in rendered
