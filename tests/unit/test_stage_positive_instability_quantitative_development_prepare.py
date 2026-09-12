from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from quanthus.stage_learning import positive_instability_quantitative_development_prepare as api
from quanthus.stage_learning.positive_instability_quantitative_development import (
    load_preregistration,
)
from quanthus.stage_learning.positive_instability_quantitative_development_prepare import (
    _parser,
    _rank,
    build_review_export,
    select_development_cases,
)
from quanthus.structure_v2_inputs import digest


def test_verify_cli_matches_stage_worker_contract() -> None:
    revision = "a" * 40
    args = _parser().parse_args(
        ["verify", "--artifact", "/tmp/existing-artifact", "--code-revision", revision]
    )
    assert args.command == "verify"
    assert args.artifact == Path("/tmp/existing-artifact")
    assert args.code_revision == revision


def test_review_export_is_candidate_blind_and_uses_last_21_rows(
    tmp_path: Path, monkeypatch,
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    source_rows = {}
    refs = []
    for index in range(25):
        row = {
            "market_date": f"2019-01-{index + 1:02d}",
            "high": str(100 + index),
            "low": str(90 + index),
            "close": str(95 + index),
            "coordinate_epoch": "epoch-1",
            "reset_reason": None,
        }
        ref = digest(row)
        source_rows[ref] = row
        refs.append(ref)
    body = {
        "security_code": "000001",
        "security_id": "security-1",
        "market_date": "2019-01-25",
        "sampling_stratum": "q1",
        "coordinate_ref": digest({"coordinate": 1}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": {"range_width": {"value": "0.2"}},
        "recent": {"width_change": {"value": "9.9"}},
        "history_refs": refs,
    }
    case = {"case_id": digest(body), **body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    paths = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {"value": "0.3"},
                "structure.geometry.counter-direction-excursion-share": {"value": "0.7"},
            }
        }
    }
    (artifact / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    (artifact / "path-facts.json").write_text(json.dumps(paths), encoding="utf-8")
    monkeypatch.setattr(
        api,
        "verify_artifact",
        lambda *_args, **_kwargs: {
            "run_id": "run-1",
            "manifest_sha256": "sha256:" + "1" * 64,
            "development_selected_case_count": 1,
            "development_evidence_digest": "sha256:" + "2" * 64,
            "path_facts_digest": "sha256:" + "3" * 64,
        },
    )

    exported = build_review_export(
        artifact,
        job_id="stage_" + "4" * 32,
        code_revision="a" * 40,
    )
    assert exported["case_count"] == 1
    assert exported["page_count"] == 1
    assert exported["threshold_basis_included"] is False
    assert exported["candidate_output_included"] is False
    assert exported["preserved_2021_evaluation_included"] is False
    review_case = exported["cases"][0]
    assert len(review_case["history"]) == 21
    assert review_case["history"][0][0] == refs[-21]
    assert "recent" not in review_case
    assert "threshold_basis" not in exported
    assert "candidate_grid" not in exported


def test_review_export_cli_contract() -> None:
    args = _parser().parse_args(
        [
            "review-export",
            "--artifact",
            "/tmp/existing-artifact",
            "--job-id",
            "stage_" + "4" * 32,
            "--code-revision",
            "a" * 40,
            "--output",
            "/tmp/review.json",
        ]
    )
    assert args.command == "review-export"
    assert args.artifact == Path("/tmp/existing-artifact")
    assert args.output == Path("/tmp/review.json")


def test_development_selection_is_hash_quarter_only_and_no_backfill() -> None:
    config = load_preregistration()
    plan = config["development_selection"]
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    seed = str(plan["sampling_seed"])
    ranked = sorted(
        pool,
        key=lambda identity: (_rank(seed, "development-id", identity), identity),
    )[:160]
    rows = []
    for index, identity in enumerate(ranked):
        quarter = index // 40 + 1
        month = (
            (12 if quarter != 4 else 1)
            if index == 9
            else {1: 2, 2: 5, 3: 8, 4: 11}[quarter]
        )
        rows.append(
            {
                "security_code": pool[identity],
                "market_date": date(2019, month, 15),
                "range_width20": "0.20",
                "width_change20": "0.10",
                "center_shift20_abs": "0.01",
                "efficiency20_abs": "0.10",
                "range_traversal20": "0.20",
                "counter_direction_excursion_share20": "0.80",
            }
        )
    selection = select_development_cases(pool, rows, config)
    assert selection["planned_identity_count"] == 160
    assert selection["selected_case_count"] == 159
    assert selection["shortfall_count"] == 1
    assert selection["shortfalls"] == [{"security_id": ranked[9], "quarter": 1}]
    assert selection["selection_uses_morphology"] is False
    assert selection["cross_quarter_backfill"] is False
    assert selection["identity_backfill"] is False


def test_date_selection_is_feature_value_blind() -> None:
    config = load_preregistration()
    plan = config["development_selection"]
    seed = str(plan["sampling_seed"])
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    ranked = sorted(
        pool,
        key=lambda identity: (_rank(seed, "development-id", identity), identity),
    )
    identity = ranked[0]
    code = pool[identity]
    rows = [
        {
            "security_code": code,
            "market_date": date(2019, 2, 1),
            "range_width20": "0.01",
            "width_change20": "9.0",
            "center_shift20_abs": "0.9",
            "efficiency20_abs": "0.01",
            "range_traversal20": "0.01",
            "counter_direction_excursion_share20": "1.0",
        },
        {
            "security_code": code,
            "market_date": date(2019, 3, 1),
            "range_width20": "0.99",
            "width_change20": "-0.9",
            "center_shift20_abs": "0.0",
            "efficiency20_abs": "0.99",
            "range_traversal20": "0.99",
            "counter_direction_excursion_share20": "0.0",
        },
    ]
    selection = select_development_cases(pool, rows, config)
    selected = selection["selected"]
    assert len(selected) == 1
    expected = min(
        rows,
        key=lambda row: (
            _rank(seed, "development-date", identity, row["market_date"]),
            row["market_date"],
        ),
    )
    assert selected[0]["market_date"] == expected["market_date"].isoformat()
