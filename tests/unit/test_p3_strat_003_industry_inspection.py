"""Synthetic boundaries for P3-STRAT-003-B-R1 industry inspection."""

from __future__ import annotations

import hashlib
import json
from datetime import date
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
        legacy_by_code={
            ("600003", "SSE"): ["legacy-600003"],
        },
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
    assert observed["p4_code_venue_only_reference_match_count"] == 1
    assert observed["legacy_p3_code_venue_only_match_count"] == 1
    assert "candidate_event_count" not in observed


def test_candidate_inventory_counts_distinct_security_identity(
    tmp_path: Path,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    run_root = tmp_path / "event-runs"
    candidate = run_root / ".staging/job_failed/1/p3-strat-003-input/candidate-daily.parquet"
    candidate.parent.mkdir(parents=True)
    connection = duckdb.connect(":memory:")
    try:
        escaped = str(candidate).replace("'", "''")
        connection.execute(
            f"""
            COPY (
              SELECT * FROM (
                VALUES
                  ('sec-a','co-a','list-a','600001','SSE',DATE '2024-12-30'),
                  ('sec-a','co-a','list-a','600001','SSE',DATE '2025-01-02'),
                  ('sec-b','co-b','list-b','000001','SZSE',DATE '2025-01-02')
              ) AS t(
                dwd_security_id,
                dwd_company_id,
                dwd_listing_id,
                listing_code,
                venue_id,
                session_date
              )
            ) TO '{escaped}' (FORMAT PARQUET)
            """
        )
    finally:
        connection.close()

    receipt, identities = inspection._candidate_inventory(
        run_root,
        {
            "job_id": "job_failed",
            "claim_generation": 1,
            "candidate_relative_path": "p3-strat-003-input/candidate-daily.parquet",
        },
        decision_start=date(2025, 1, 1),
        decision_end=date(2026, 9, 17),
    )
    assert receipt["row_count"] == 3
    assert receipt["security_count"] == 2
    assert receipt["decision_security_count"] == 2
    assert len(identities) == 2
    assert identities[0]["security_id"] == "sec-a"
    assert identities[0]["in_decision_scope"] is True
    assert receipt["claim_execution_time_bytes_reproduced"] is False


def test_candidate_inventory_rejects_security_company_ambiguity(
    tmp_path: Path,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    run_root = tmp_path / "event-runs"
    candidate = run_root / ".staging/job_failed/1/p3-strat-003-input/candidate-daily.parquet"
    candidate.parent.mkdir(parents=True)
    connection = duckdb.connect(":memory:")
    try:
        escaped = str(candidate).replace("'", "''")
        connection.execute(
            f"""
            COPY (
              SELECT * FROM (
                VALUES
                  ('sec-a','co-a','list-a','600001','SSE',DATE '2025-01-02'),
                  ('sec-a','co-b','list-a','600001','SSE',DATE '2025-01-03')
              ) AS t(
                dwd_security_id,
                dwd_company_id,
                dwd_listing_id,
                listing_code,
                venue_id,
                session_date
              )
            ) TO '{escaped}' (FORMAT PARQUET)
            """
        )
    finally:
        connection.close()

    with pytest.raises(ExpertEvaluationError, match="ambiguous security identity"):
        inspection._candidate_inventory(
            run_root,
            {
                "job_id": "job_failed",
                "claim_generation": 1,
                "candidate_relative_path": ("p3-strat-003-input/candidate-daily.parquet"),
            },
            decision_start=date(2025, 1, 1),
            decision_end=date(2026, 9, 17),
        )


def test_coverage_reports_ambiguous_legacy_code_venue_matches() -> None:
    identity = {
        "security_id": "sec-x",
        "company_id": "co-x",
        "listing_code": "600999",
        "venue_id": "SSE",
        "first_session": "2025-01-02",
        "last_session": "2026-09-18",
        "in_decision_scope": True,
    }
    observed = inspection._coverage(
        [identity],
        legacy={},
        legacy_by_code={
            ("600999", "SSE"): ["legacy-a", "legacy-b"],
        },
        dwd={},
        dwd_reasons={"sec-x": "no_company_industry_membership"},
        p4={},
        p4_by_code={},
        sample_limit=20,
    )
    assert observed["remaining_unresolved_security_count"] == 1
    assert observed["legacy_p3_code_venue_only_match_count"] == 0
    assert observed["legacy_p3_code_venue_ambiguous_match_count"] == 1
    assert observed["remaining_unresolved_samples"][0]["legacy_p3_code_venue_match_count"] == 2


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
        legacy_by_code={},
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
    qualification = json.loads((ROOT / raw["qualification"]["path"]).read_text(encoding="utf-8"))
    qualification_path = project / raw["qualification"]["path"]
    qualification_path.parent.mkdir(parents=True, exist_ok=True)
    qualification_data = json.dumps(
        qualification,
        sort_keys=True,
    ).encode()
    qualification_path.write_bytes(qualification_data)
    raw["qualification"]["file_sha256"] = "sha256:" + hashlib.sha256(qualification_data).hexdigest()
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
        output=(event_root / ".staging/job_new/1/p3-strat-003-industry-inspection"),
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

    manifest = json.loads((missing_candidate.output / "manifest.json").read_text(encoding="utf-8"))
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

    def unavailable(
        _runtime: object,
        *,
        json_limit: int,
    ) -> tuple[dict[str, str], dict[str, object]]:
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
    manifest = json.loads((missing_candidate.output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inspection"]["industry_sources"]["status"] == "unavailable_or_invalid"
    assert manifest["inspection"]["p6_mutation"] is False


def test_corrupt_retained_projection_seals_unavailable(missing_candidate: SimpleNamespace) -> None:
    raw = json.loads(missing_candidate.request.read_text())
    failed = raw["failed_job"]
    staging = missing_candidate.output.parents[2]
    candidate = (
        staging
        / failed["job_id"]
        / str(failed["claim_generation"])
        / failed["candidate_relative_path"]
    )
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"invalid parquet")
    result = inspection.build_industry_inspection(
        request_path=missing_candidate.request,
        output=missing_candidate.output,
        code_revision="c" * 40,
    )
    assert result["disposition"] == inspection.UNAVAILABLE
    manifest = json.loads((missing_candidate.output / "manifest.json").read_text())
    assert manifest["inspection"]["candidate_projection"]["reason_code"] == (
        "p3_strat_003_industry_inspection_source_unavailable"
    )


@pytest.fixture
def sealed_industry_base(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "base"
    files = []
    for table in ("dwd_company_industry_membership", "dwd_industry_node"):
        path = root / "tables" / (table + ".parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(table.encode())
        files.append(
            {
                "path": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "sha256": inspection._sha256(path),
            }
        )
    (root / "manifest.json").write_text(json.dumps({"files": files}))
    digest = inspection._sha256(root / "manifest.json")
    (root / "READY").write_text(digest + "\n")
    return root, digest


@pytest.mark.parametrize("tamper", ["none", "manifest", "READY", "payload", "missing", "symlink"])
def test_dwd_industry_verifies_seal_before_scan(
    sealed_industry_base: tuple[Path, str],
    tamper: str,
) -> None:
    root, digest = sealed_industry_base
    payload = root / "tables/dwd_industry_node.parquet"
    if tamper == "manifest":
        (root / "manifest.json").write_text("{}")
    elif tamper == "READY":
        (root / "READY").write_text("sha256:" + "0" * 64)
    elif tamper == "payload":
        payload.write_bytes(b"x" * payload.stat().st_size)
    elif tamper == "missing":
        payload.unlink()
    elif tamper == "symlink":
        moved = root / "moved"
        payload.rename(moved)
        payload.symlink_to(moved)
    if tamper == "none":
        inspection._verify_dwd_industry_source(root, manifest_sha256=digest)
    else:
        with pytest.raises(ExpertEvaluationError):
            inspection._verify_dwd_industry_source(root, manifest_sha256=digest)


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
