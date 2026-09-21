"""Synthetic boundaries for P3-STRAT-003-B-R1 industry inspection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    digest_value,
)
from quanthus.expert_strategy import p3_strat_003_industry_inspection as inspection
from quanthus.platform.event_worker import (
    _structure_input,
    _structure_request_digest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p3-strat-003/industry-inspection.v1.json"


def test_frozen_industry_inspection_config_is_admitted() -> None:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert digest_value(raw) == inspection.REQUEST_DIGEST
    job = {
        "code_revision": "a" * 40,
        "run_config": str(CONFIG.relative_to(ROOT)),
    }
    assert _structure_input(job) == job
    assert _structure_request_digest(job["run_config"]) == inspection.REQUEST_DIGEST
    with pytest.raises(RuntimeError, match="run_config_not_governed"):
        _structure_input(
            {
                **job,
                "run_config": "config/research/p3-strat-003/not-governed.json",
            }
        )


def test_coverage_keeps_event_occurrences_separate_from_security_count() -> None:
    identities = [
        {
            "security_id": "sec-a",
            "company_id": "co-a",
            "listing_code": "600001",
            "venue_id": "SSE",
            "first_session": "2024-10-01",
            "last_session": "2026-09-18",
            "in_decision_scope": True,
        },
        {
            "security_id": "sec-b",
            "company_id": "co-b",
            "listing_code": "000001",
            "venue_id": "SZSE",
            "first_session": "2024-10-01",
            "last_session": "2026-09-18",
            "in_decision_scope": True,
        },
        {
            "security_id": "sec-c",
            "company_id": "co-c",
            "listing_code": "600003",
            "venue_id": "SSE",
            "first_session": "2024-10-01",
            "last_session": "2024-12-31",
            "in_decision_scope": False,
        },
    ]
    observed = inspection._coverage(
        identities,
        legacy={"600001": "legacy"},
        dwd={"sec-a": "industry-1"},
        dwd_reasons={
            "sec-a": "covered",
            "sec-b": "no_company_industry_membership",
            "sec-c": "no_company_industry_membership",
        },
        p4={"sec-b": ("industry-2", "000001", "SZSE")},
        p4_by_code={
            ("000001", "SZSE"): ["sec-b"],
            ("600003", "SSE"): ["older-sec-c"],
        },
        sample_limit=20,
    )
    assert observed["all_security_status_counts"] == {
        "dwd_native": 1,
        "no_company_industry_membership": 1,
        "p4_sealed_reference": 1,
    }
    assert observed["decision_security_status_counts"] == {
        "dwd_native": 1,
        "p4_sealed_reference": 1,
    }
    assert observed["remaining_unresolved_security_count"] == 1
    assert observed["code_venue_only_reference_match_count"] == 1
    assert "candidate_event_count" not in observed


def test_coverage_reports_dwd_p4_industry_conflicts() -> None:
    identity = {
        "security_id": "sec-a",
        "company_id": "co-a",
        "listing_code": "600001",
        "venue_id": "SSE",
        "first_session": "2025-01-02",
        "last_session": "2026-09-18",
        "in_decision_scope": True,
    }
    observed = inspection._coverage(
        [identity],
        legacy={},
        dwd={"sec-a": "industry-dwd"},
        dwd_reasons={"sec-a": "covered"},
        p4={"sec-a": ("industry-p4", "600001", "SSE")},
        p4_by_code={("600001", "SSE"): ["sec-a"]},
        sample_limit=20,
    )
    assert observed["remaining_unresolved_security_count"] == 0
    assert observed["dwd_p4_conflict_count"] == 1
    assert observed["conflict_samples"][0]["security_id"] == "sec-a"


@pytest.fixture
def missing_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    project = tmp_path / "project"
    event_root = tmp_path / "event-runs"
    request = project / CONFIG.relative_to(ROOT)
    request.parent.mkdir(parents=True)
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    qualification = json.loads(
        (ROOT / raw["qualification"]["path"]).read_text(encoding="utf-8")
    )
    qualification_path = project / raw["qualification"]["path"]
    qualification_path.parent.mkdir(parents=True, exist_ok=True)
    qualification_data = json.dumps(
        qualification,
        sort_keys=True,
    ).encode()
    qualification_path.write_bytes(qualification_data)
    raw["qualification"]["file_sha256"] = (
        "sha256:" + hashlib.sha256(qualification_data).hexdigest()
    )
    request.write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.setattr(inspection, "REQUEST_DIGEST", digest_value(raw))
    monkeypatch.setattr(inspection, "find_project_root", lambda _path: project)
    monkeypatch.setattr(inspection, "_request", lambda _path: qualification)
    monkeypatch.setattr(inspection, "_run_root", lambda: event_root)

    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(
        inspection,
        "load_prospective_runtime_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            historical_base_root=base,
            static_industry_mapping=tmp_path / "legacy.json",
            static_industry_mapping_sha256="sha256:" + "0" * 64,
        ),
    )
    return SimpleNamespace(
        request=request,
        output=(
            event_root
            / ".staging/job_new/1/p3-strat-003-industry-inspection"
        ),
    )


def test_missing_projection_seals_unavailable_without_opening_industry_sources(
    missing_candidate: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("industry source must not be opened")

    monkeypatch.setattr(inspection, "_p4_reference", forbidden)
    monkeypatch.setattr(inspection, "_dwd_reference", forbidden)
    monkeypatch.setattr(inspection, "_legacy_reference", forbidden)
    result = inspection.build_industry_inspection(
        request_path=missing_candidate.request,
        output=missing_candidate.output,
        code_revision="c" * 40,
    )
    assert result["disposition"] == inspection.UNAVAILABLE
    assert result["b_acceptance"] is False

    manifest = json.loads(
        (missing_candidate.output / "manifest.json").read_text(encoding="utf-8")
    )
    core = manifest["inspection"]
    assert core["candidate_projection"]["status"] == "missing_or_unreadable"
    assert core["strategy_or_economic_results_read"] is False
    assert core["target_outcome_labels_read"] is False
    assert core["daily_current_mutation"] is False


def test_source_failure_seals_unavailable_instead_of_triggering_fallback(
    missing_candidate: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "status": "observed_after_failure",
        "security_count": 1,
        "decision_security_count": 1,
    }
    identities = [
        {
            "security_id": "sec-a",
            "company_id": "co-a",
            "listing_code": "600001",
            "venue_id": "SSE",
            "first_session": "2025-01-02",
            "last_session": "2026-09-18",
            "in_decision_scope": True,
        }
    ]
    monkeypatch.setattr(
        inspection,
        "_candidate_inventory",
        lambda *_args, **_kwargs: (candidate, identities),
    )

    def unavailable(_runtime: object) -> tuple[dict[str, str], dict[str, object]]:
        raise ExpertEvaluationError(
            "p3_strat_003_industry_inspection_source_unavailable",
            "legacy source unavailable",
        )

    monkeypatch.setattr(inspection, "_legacy_reference", unavailable)
    result = inspection.build_industry_inspection(
        request_path=missing_candidate.request,
        output=missing_candidate.output,
        code_revision="c" * 40,
    )
    assert result["disposition"] == inspection.UNAVAILABLE
    manifest = json.loads(
        (missing_candidate.output / "manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["inspection"]["industry_sources"]["status"]
        == "unavailable_or_invalid"
    )
    assert manifest["inspection"]["p6_mutation"] is False


def test_sealed_industry_inspection_detects_summary_tamper(
    missing_candidate: SimpleNamespace,
) -> None:
    result = inspection.build_industry_inspection(
        request_path=missing_candidate.request,
        output=missing_candidate.output,
        code_revision="c" * 40,
    )
    assert (
        inspection.verify_industry_inspection_artifact(
            missing_candidate.output,
            code_revision="c" * 40,
            request_digest=inspection.REQUEST_DIGEST,
        )
        == result
    )
    (missing_candidate.output / "summary.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    with pytest.raises(ExpertEvaluationError, match="artifact boundary"):
        inspection.verify_industry_inspection_artifact(
            missing_candidate.output,
            code_revision="c" * 40,
            request_digest=inspection.REQUEST_DIGEST,
        )
