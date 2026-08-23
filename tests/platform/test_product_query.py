from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.platform.models import CandidateActiveRunRecord, CandidateRunRecord
from quanthus.platform.product_query import ProductCandidateEvidence, ProductResearchCatalog
from quanthus.policy import ConfigurationError, find_project_root

CATALOG = find_project_root() / "config" / "product-research-catalog.v1.json"
EVIDENCE = find_project_root() / "config" / "product-candidate-evidence.v1.json"


def test_current_product_catalog_contains_candidates_only() -> None:
    catalog = ProductResearchCatalog(CATALOG).read()
    assert catalog["data_scope"]["dwd_base"] == {
        "build_id": "V2-Build202607",
        "package_id": "dwd-base-202607-v2.1",
        "correction_id": "dwd-base-202607-v2.1-correction-20260813-001",
        "correction_content_digest": (
            "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da"
        ),
        "through_date": "2026-07-31",
        "coverage_start": "dataset-specific",
    }
    assert set(catalog["data_scope"]["replay_partitions"]) == {
        "development",
        "arbitration",
        "permanent_blind",
        "recent_research",
    }
    assert len(catalog["strategies"]) == 6
    assert all(item["kind"] == "candidate-version" for item in catalog["strategies"])
    assert catalog["latest_candidate_batch"] == {
        "batch_id": "candidate-batch-e8a022725bee32e6",
        "batch_digest": (
            "sha256:2c18101c67d3dcd9267cdf6d1fe0aedeea3dc756f716855c850ff405792f3523"
        ),
        "completed_at": "2026-08-13T03:42:26.097551+00:00",
        "scope_start": "2012-01-04",
        "scope_end": "2021-12-30",
        "candidate_count": 5,
        "succeeded_count": 5,
        "failed_count": 0,
        "shared_provider": True,
        "dedicated_ads_required": False,
        "governance_transition_applied": False,
        "execution_authorized": False,
    }
    assert not any(
        item["governance_decision"] == "research-admitted"
        for item in catalog["strategies"]
    )
    assert not any(item["execution_authorized"] for item in catalog["strategies"])
    assert all(
        item["evidence_scope"]["decision_start"] == "2012-01-04"
        and item["evidence_scope"]["decision_end"] == "2021-12-30"
        for item in catalog["strategies"]
        if item.get("replay_submission_eligible", True)
    )
    expert = next(
        item
        for item in catalog["strategies"]
        if item["strategy_id"] == "candidate.p4-trend-risk"
    )
    assert expert["evidence_scope"] == {
        "partition": "development",
        "decision_start": "2020-01-01",
        "decision_end": "2021-12-30",
        "execution_end": "2021-12-31",
    }
    assert expert["replay_submission_eligible"] is False
    external = next(
        item for item in catalog["strategies"] if item["source_system"] == "quantdinger"
    )
    assert external["operations"] == 36
    assert external["closed_trades"] == 18
    assert external["total_return"] == 59.628843
    assert external["evidence_ref"] == (
        "docs/acceptance/dwd-v2-1-external-findings-hotfix-20260813.md"
    )
    assert external["external_conformance"]["signal_match"] == (
        "37 shared + 1 governed downstream divergence"
    )
    assert external["external_conformance"]["fill_date_side_match"] == (
        "36 shared / 38 native"
    )
    assert external["external_conformance"]["legacy_direct_dwd_v2_1"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", "system-strategy", "candidates only"),
        ("execution_authorized", True, "cannot grant execution authority"),
        ("governance_decision", "research-admitted", "replay facts only"),
    ],
)
def test_catalog_rejects_candidate_state_shortcuts(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["strategies"][0][field] = value
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        ProductResearchCatalog(path).read()


def test_catalog_rejects_dwd_and_candidate_scope_drift(tmp_path: Path) -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["data_scope"]["dwd_base"]["through_date"] = "2021-12-31"
    path = tmp_path / "catalog-dwd-drift.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="DWD base is invalid"):
        ProductResearchCatalog(path).read()

    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["strategies"][0]["evidence_scope"]["execution_end"] = "2026-07-30"
    path = tmp_path / "catalog-evidence-drift.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="exceeds its replay partition"):
        ProductResearchCatalog(path).read()


def test_catalog_rejects_unbound_quantdinger_conformance(tmp_path: Path) -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    external = next(
        item for item in payload["strategies"] if item["source_system"] == "quantdinger"
    )
    external["external_conformance"]["signal_match"] = "38/38"
    path = tmp_path / "catalog-unbound-external.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="lacks direct DWD conformance evidence"):
        ProductResearchCatalog(path).read()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("correction_id", "dwd-base-202607-v2.1-correction-unbound"),
        ("correction_content_digest", "sha256:unbound"),
    ],
)
def test_catalog_rejects_unbound_dwd_correction(
    tmp_path: Path, field: str, value: str
) -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["data_scope"]["dwd_base"][field] = value
    path = tmp_path / "catalog-unbound-correction.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="DWD base is invalid"):
        ProductResearchCatalog(path).read()


def test_catalog_rejects_quantdinger_result_not_bound_to_correction(tmp_path: Path) -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    external = next(
        item for item in payload["strategies"] if item["source_system"] == "quantdinger"
    )
    external["data_lineage"]["corporate_action_revision"] = (
        "corporate_action_replay_2012_20260730_v3"
    )
    path = tmp_path / "catalog-side-path-revision.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="lacks direct DWD conformance evidence"):
        ProductResearchCatalog(path).read()


def test_candidate_evidence_covers_catalog_with_real_curves_and_trades() -> None:
    catalog = ProductResearchCatalog(CATALOG)
    evidence = ProductCandidateEvidence(EVIDENCE, catalog).read()
    assert len(evidence["strategies"]) == 6
    expert = next(
        item
        for item in evidence["strategies"]
        if item["strategy_id"] == "candidate.p4-trend-risk"
    )
    assert expert["strategy_version"] == "1.0.1"
    assert len(expert["equity_curve"]) == 25
    assert expert["trades"] == []
    quantdinger = next(
        item
        for item in evidence["strategies"]
        if item["strategy_id"] == "quantdinger.strategy-v2-bullish-three-lines"
    )
    assert len(quantdinger["equity_curve"]) == 121
    assert len(quantdinger["trades"]) == 18
    assert quantdinger["performance"]["win_rate"] == 44.444444


def test_candidate_evidence_rejects_content_tampering(tmp_path: Path) -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    payload["strategies"][0]["trades"][0]["pnl"] = 999999
    path = tmp_path / "tampered-evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="identity is invalid"):
        ProductCandidateEvidence(path, ProductResearchCatalog(CATALOG)).read()


class _ActiveRunStore:
    def __init__(
        self, active: CandidateActiveRunRecord, run: CandidateRunRecord
    ) -> None:
        self.active = active
        self.run = run

    def candidate_active_runs(self) -> tuple[CandidateActiveRunRecord, ...]:
        return (self.active,)

    def candidate_run(self, run_id: str) -> CandidateRunRecord | None:
        return self.run if run_id == self.run.run_id else None


def test_active_run_evidence_overrides_bootstrap_projection(tmp_path: Path) -> None:
    catalog = ProductResearchCatalog(CATALOG)
    bootstrap = ProductCandidateEvidence(EVIDENCE, catalog).candidate("baseline.ma-trend")
    assert bootstrap is not None
    raw = {
        "strategy_id": "baseline.ma-trend",
        "strategy_version": "2.0.0",
        "performance": {
            "initial_equity": "100000",
            "ending_equity": "112345",
            "total_pnl": "12345",
            "total_fees": "123",
            "total_return": "0.12345",
            "maximum_drawdown": "0.0456",
            "win_rate": "0.5",
            "sharpe_ratio": "1.25",
            "exposure_rate": "0.2",
            "profit_factor": "1.5",
            "average_holding_sessions": "8",
            "turnover": "0.3",
            "return_excluding_top_3_closed_trades": "0.08",
            "capital_matched_benchmark_total_return": "0.04",
            "closed_trades": 1,
            "operation_count": 2,
        },
        "equity_curve": [
            {"market_date": "2012-01-04", "system_equity": "100000"},
            {"market_date": "2012-01-31", "system_equity": "112345"},
        ],
        "trades": [
            {
                "security_id": "600000",
                "entry_date": "2012-01-05",
                "exit_date": "2012-01-20",
                "holding_sessions": 10,
                "entry_quantity": 100,
                "pnl": "12345",
                "return_rate": "0.12345",
                "dividends": "0",
                "entry_fees": "50",
                "exit_fees": "73",
            }
        ],
        "backtest": {
            "manifest": {
                "run_id": "strategy-run",
                "data_snapshot_id": "dwd-job-local",
                "execution_policy_version": "p2-daily-research-v1",
                "execution_profile_id": "close-to-next-open-v1",
                "schedule_version": "schedule-v1",
                "rule_version": "2.0.0",
                "feature_version": "features-v1",
                "parameter_hash": "sha256:parameter",
                "official": False,
                "result_summary": {"fills": 2},
            }
        },
    }
    encoded = (json.dumps(raw, sort_keys=True) + "\n").encode()
    evidence_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    run_root = tmp_path / "candidate-runs"
    evidence_path = run_root / "runs/run_active/evidence/baseline.ma-trend-2.0.0.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(encoded)
    now = datetime.now(timezone.utc)
    run = CandidateRunRecord(
        run_id="run_active",
        job_id="job_active",
        claim_generation=1,
        input_digest="sha256:input",
        artifact_ref="/data/candidate-runs/runs/run_active",
        result_sha256="sha256:result",
        candidates=(
            {
                "candidate_id": "baseline.ma-trend",
                "candidate_version": "2.0.0",
                "artifact": "evidence/baseline.ma-trend-2.0.0.json",
                "artifact_sha256": evidence_digest,
                "summary": {
                    "operations": 2,
                    "closed_trades": 1,
                    "total_return": "0.12345",
                    "maximum_drawdown": "0.0456",
                },
            },
        ),
        active_switch_applied=True,
        created_at=now,
    )
    active = CandidateActiveRunRecord(
        strategy_id="baseline.ma-trend",
        strategy_version="2.0.0",
        run_id=run.run_id,
        evidence_digest=evidence_digest,
        updated_at=now,
    )
    evidence_reader = ProductCandidateEvidence(
        EVIDENCE,
        catalog,
        store=_ActiveRunStore(active, run),
        run_root=run_root,
    )
    evidence_reader.warm_active()
    evidence = evidence_reader.candidate("baseline.ma-trend")
    assert evidence is not None
    assert evidence["source_artifact_sha256"] == evidence_digest
    assert evidence["summary"] == {
        "operations": 2,
        "closed_trades": 1,
        "total_return": 12.345,
        "max_drawdown": 4.5600000000000005,
    }
    assert evidence["trades"][0]["security_code"] == "600000"
    assert evidence["performance"]["total_fees"] == 123.0

    evidence_path.unlink()
    cached = evidence_reader.candidate("baseline.ma-trend")
    assert cached == evidence


def test_active_run_summary_does_not_read_large_evidence(tmp_path: Path) -> None:
    catalog = ProductResearchCatalog(CATALOG)
    now = datetime.now(timezone.utc)
    evidence_digest = "sha256:active-evidence"
    run = CandidateRunRecord(
        run_id="run_active",
        job_id="job_active",
        claim_generation=1,
        input_digest="sha256:input",
        artifact_ref="/data/candidate-runs/runs/run_active",
        result_sha256="sha256:result",
        candidates=(
            {
                "candidate_id": "baseline.ma-trend",
                "candidate_version": "2.0.0",
                "artifact": "evidence/missing.json",
                "artifact_sha256": evidence_digest,
                "summary": {
                    "operations": 12,
                    "closed_trades": 6,
                    "total_return": "0.25",
                    "maximum_drawdown": "0.10",
                },
            },
        ),
        active_switch_applied=True,
        created_at=now,
    )
    active = CandidateActiveRunRecord(
        strategy_id="baseline.ma-trend",
        strategy_version="2.0.0",
        run_id=run.run_id,
        evidence_digest=evidence_digest,
        updated_at=now,
    )
    evidence = ProductCandidateEvidence(
        EVIDENCE,
        catalog,
        store=_ActiveRunStore(active, run),
        run_root=tmp_path,
    )

    assert evidence.candidate_summary("baseline.ma-trend") == {
        "source_artifact_sha256": evidence_digest,
        "summary": {
            "operations": 12,
            "closed_trades": 6,
            "total_return": 25.0,
            "max_drawdown": 10.0,
        },
    }
