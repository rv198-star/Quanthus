from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_reference_protocol import PROTOCOL_DIGEST, build_evidence
from quanthus.stage_learning.order_rule_prepare import (
    ARTIFACT_SCHEMA,
    SUMMARY_SCHEMA,
    OrderRulePrepareError,
    load_prepare_config,
    verify_prepare_artifact,
)
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST
from quanthus.stage_learning.order_rule_validation import (
    VALIDATION_DIGEST,
    SamplingObservation,
)
from quanthus.structure_v2_inputs import DailyInput, InputPolicy, canonical, digest

ROOT = find_project_root()
REVISION = "b" * 40
SHA = "sha256:" + "a" * 64


def rows() -> list[DailyInput]:
    result = []
    close = Decimal("100")
    start = date(2020, 11, 1)
    for index in range(90):
        day = start + timedelta(days=index)
        close += Decimal("1.2") if index % 5 in {0, 1, 2} else Decimal("-0.9")
        result.append(DailyInput(
            security_code="000001",
            market_date=day,
            session_index=index,
            cutoff=datetime.combine(day, time(15, 30), ZoneInfo("Asia/Shanghai")),
            security_id="security-A",
            listing_id="listing-A",
            exchange="SZSE",
            high=close + Decimal("2"),
            low=close - Decimal("2"),
            close=close,
            volume=Decimal("1000"),
            source_record_id=f"source-{day}",
            source_revision="revision-v1",
            source_artifact_sha256=SHA,
            dwd_known_from="2026-07-01T00:00:00+00:00",
            coordinate_epoch="initial-epoch",
            finality="final",
            calendar_member_digest=SHA,
            action_member_digest=SHA,
        ))
    return result


def evidence() -> dict:
    values = rows()
    case_day = values[-1].market_date
    return build_evidence(
        values,
        [("000001", case_day)],
        InputPolicy(PROTOCOL_DIGEST, ("000001",), "calendar-v1"),
        code_revision=REVISION,
        source_kind="retrospective_development",
        sampling_strata={("000001", case_day): "representative"},
    )


def artifact(tmp_path: Path, *, candidate_scored: bool = False) -> Path:
    retained = evidence()
    case = retained["cases"][0]
    observation = SamplingObservation.from_case(case)
    selected = [{
        "security_id": case["security_id"],
        "security_code": case["security_code"],
        "market_date": case["market_date"],
        "panel": "coverage",
        "bucket": "coverage",
        "quarter": observation.quarter,
        "observation_digest": digest(observation),
    }]
    selection_body = {
        "schema_version": "quanthus.stage-order-rule-selection.v1",
        "validation_digest": VALIDATION_DIGEST,
        "pool_digest": digest({"security-A": "000001"}),
        "pool_size": 1,
        "scanned_attempts": 1,
        "selected": selected,
        "coverage_slots": {"security-A": observation.quarter},
        "shortfalls": {"coverage": 63, "order_like": 48, "instability_like": 32, "boundary": 16},
        "source_isolation_verified": False,
        "runtime_activation_authorized": False,
    }
    selection = {**selection_body, "selection_digest": digest(selection_body)}
    binding = {
        "schema_version": "quanthus.stage-order-rule-source-binding.v1",
        "source_isolation_verified": True,
        "old_160_contexts_read_back": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-007-B",
        "code_revision": REVISION,
        "config_digest": digest(
            load_prepare_config(ROOT / "config/stage-learning/order-rule-prepare.v1.json")
        ),
        "rule_digest": RULE_DIGEST,
        "validation_digest": VALIDATION_DIGEST,
        "reference_protocol_digest": PROTOCOL_DIGEST,
        "source_isolation_verified": True,
        "old_cohort_size": 512,
        "excluded_economic_identity_count": 515,
        "candidate_population_count": 1000,
        "pool_size": 1,
        "source_attempt_count": 90,
        "sampling_attempt_count": 1,
        "fact_build_counts": {"securities": 1, "fact_intersection": 1, "fact_unavailable": 0},
        "selected_case_count": 1,
        "selection_digest": selection["selection_digest"],
        "selection_shortfalls": selection["shortfalls"],
        "evidence_digest": digest(retained),
        "evidence_source_row_count": len(retained["source_rows"]),
        "opinion_count": 0,
        "candidate_scored": candidate_scored,
        "parameter_search": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "official_labels_emitted": False,
        "primary_stage_emitted": False,
        "review_ready": True,
        "elapsed_seconds": 1.0,
    }
    root = tmp_path / ("bad" if candidate_scored else "good")
    root.mkdir()
    payloads = {
        "source-binding.json": canonical(binding),
        "selection.json": canonical(selection),
        "evidence.json": canonical(retained),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (root / name).write_bytes(raw)
    def sha(raw: bytes) -> str:
        return "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": "stage-order-rule-prepare-test",
        "code_revision": REVISION,
        "config_digest": summary["config_digest"],
        "files": {name: sha(raw) for name, raw in payloads.items()},
        "source_isolation_verified": True,
        "opinion_generation": False,
        "candidate_scoring": False,
        "parameter_search": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
    }
    raw_manifest = canonical(manifest)
    (root / "manifest.json").write_bytes(raw_manifest)
    (root / "READY").write_text(sha(raw_manifest) + "\n", encoding="ascii")
    return root


def test_prepare_config_freezes_only_source_selection_and_evidence_authority() -> None:
    config = load_prepare_config(ROOT / "config/stage-learning/order-rule-prepare.v1.json")
    assert config["bindings"] == {
        "reference_protocol_digest": PROTOCOL_DIGEST,
        "rule_digest": RULE_DIGEST,
        "validation_digest": VALIDATION_DIGEST,
    }
    assert config["authority"]["development_data_read"] is True
    for key in (
        "opinion_generation", "candidate_scoring", "parameter_search", "future_outcome_access",
        "arbitration_access", "permanent_blind_access", "official_labels", "primary_stage",
        "daily_or_strategy", "trading",
    ):
        assert config["authority"][key] is False


def test_prepare_artifact_cold_rebuild_binds_selection_and_evidence(
    tmp_path: Path,
) -> None:
    observed = verify_prepare_artifact(artifact(tmp_path), code_revision=REVISION)
    assert observed["verified"] is True
    assert observed["source_isolation_verified"] is True
    assert observed["selected_case_count"] == 1
    assert observed["opinion_count"] == 0
    assert observed["candidate_scored"] is False
    assert observed["review_ready"] is True


def test_prepare_verifier_rejects_scoring_smuggled_into_prepare(tmp_path: Path) -> None:
    with pytest.raises(OrderRulePrepareError, match="authority_or_source"):
        verify_prepare_artifact(artifact(tmp_path, candidate_scored=True), code_revision=REVISION)
