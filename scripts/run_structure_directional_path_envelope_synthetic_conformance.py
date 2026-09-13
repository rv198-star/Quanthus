from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.range_envelope_prototype import assess_close_envelope
from quanthus.structure_directional_path_envelope import (
    CONTRACT_DIGEST,
    assess_close_path_envelope,
    assess_daily_segment,
    load_contract,
)
from quanthus.structure_v2_inputs import DailyInput, canonical, digest

ROOT = find_project_root()
SYNTHETIC = ROOT / "config/structure-directional-path-envelope-synthetic-contract.v1.json"
SYNTHETIC_DIGEST = "sha256:4dcef798bc12cabf4cc0f0e67c016bc93eb504f1f9cf1bbacb9f8d04d6c6e0ea"
STAGE_SYNTHETIC = ROOT / "config/stage-learning/stage-atomic-envelope-synthetic-contract.v1.json"


def _row(
    index: int,
    close: object | None,
    *,
    listing: str = "listing-A",
    coordinate: str = "coord-A",
) -> DailyInput:
    value = None if close is None else Decimal(str(close))
    return DailyInput(
        security_code="000001",
        market_date=date(2026, 1, 1).replace(day=1 + index),
        session_index=index,
        cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        security_id="security-A",
        listing_id=listing,
        exchange="XSHG",
        high=None if value is None else value + Decimal("0.1"),
        low=None if value is None else value - Decimal("0.1"),
        close=value,
        volume=Decimal("100"),
        source_record_id=f"synthetic-{index}",
        source_revision="synthetic-v1",
        source_artifact_sha256="sha256:" + "1" * 64,
        dwd_known_from="synthetic",
        coordinate_epoch=coordinate,
        finality="final",
    )


def _selected(value: dict[str, object]) -> dict[str, object]:
    descriptors = value["descriptors"]
    assert isinstance(descriptors, dict)
    return {
        "status": value["status"],
        "escape_count": descriptors["escape_count"],
        "resolved_escape_count": descriptors["resolved_escape_count"],
        "unresolved_escape_count": descriptors["unresolved_escape_count"],
        "resolved_escape_side_sequence": descriptors["resolved_escape_side_sequence"],
        "resolved_escape_side_alternation_count": descriptors[
            "resolved_escape_side_alternation_count"
        ],
        "resolved_escape_both_sides": descriptors["resolved_escape_both_sides"],
        "unresolved_escape_sides": descriptors["unresolved_escape_sides"],
    }


def run() -> dict[str, object]:
    design = load_contract(ROOT)
    if digest(design) != CONTRACT_DIGEST:
        raise RuntimeError("design_contract_drift")
    synthetic = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
    if digest(synthetic) != SYNTHETIC_DIGEST:
        raise RuntimeError("synthetic_contract_drift")
    stage_cases = {
        item["id"]: item
        for item in json.loads(STAGE_SYNTHETIC.read_text(encoding="utf-8"))["envelope_cases"]
    }

    checks: dict[str, bool] = {}
    observed_cases = []
    by_id: dict[str, dict[str, object]] = {}
    for case in synthetic["cases"]:
        assessment = assess_close_path_envelope(case["closes"]).to_dict()
        selected = _selected(assessment)
        checks[f"case:{case['id']}"] = selected == case["expected"]
        observed = {"id": case["id"], **selected}
        observed_cases.append(observed)
        by_id[case["id"]] = observed

    scale_source = next(
        item
        for item in synthetic["cases"]
        if item["id"] == "alternating-failed-resolution"
    )
    scaled = [
        None if item is None else Decimal(str(item)) * Decimal("100")
        for item in scale_source["closes"]
    ]
    scale_base = assess_close_path_envelope(scale_source["closes"]).to_dict()
    scale_other = assess_close_path_envelope(scaled).to_dict()
    checks["invariance:positive_price_scale"] = _selected(scale_base) == _selected(scale_other)

    common_ids = {
        "orderly-high-churn-contraction",
        "one-leg-repricing-then-hold",
        "stable-narrow-noise",
        "unavailable-close",
    }
    stage_failed = stage_cases["compact-failed-resolution-disorder"]
    structure_failed = next(
        item
        for item in synthetic["cases"]
        if item["id"] == "alternating-failed-resolution"
    )
    checks["compatibility:failed_resolution_path_exact"] = (
        stage_failed["closes"] == structure_failed["closes"]
    )
    for case_id in sorted(common_ids):
        closes = stage_cases[case_id]["closes"]
        stage = assess_close_envelope(closes).to_dict()
        structure = assess_close_path_envelope(closes).to_dict()
        checks[f"compatibility:stage_e2:{case_id}"] = _selected(stage) == _selected(structure)
    stage = assess_close_envelope(stage_failed["closes"]).to_dict()
    structure = assess_close_path_envelope(structure_failed["closes"]).to_dict()
    checks["compatibility:stage_e2:failed_resolution"] = _selected(stage) == _selected(structure)

    t_escape = by_id["t-session-unresolved-escape"]
    checks["pit:T_escape_unresolved"] = (
        t_escape["unresolved_escape_count"] == 1
        and t_escape["resolved_escape_count"] == 0
        and t_escape["unresolved_escape_sides"] == ["UPPER"]
    )

    base_closes = next(
        item for item in synthetic["cases"] if item["id"] == "stable-narrow-noise"
    )["closes"]
    rows = [_row(index, close) for index, close in enumerate(base_closes)]
    valid = assess_daily_segment(rows).to_dict()
    checks["segment:valid"] = valid["status"] == "AVAILABLE"

    changed = list(rows)
    changed[-1] = _row(20, base_closes[-1], listing="listing-B", coordinate="coord-B")
    listing_reset = assess_daily_segment(changed).to_dict()
    checks["segment:listing_or_coordinate_rewarm"] = (
        listing_reset["status"] == "INSUFFICIENT_HISTORY"
        and listing_reset["reason"] == "listing_or_coordinate_change_requires_rewarm"
    )

    gap = list(rows)
    original = gap[-1]
    gap[-1] = DailyInput(**{**original.__dict__, "session_index": 99})
    gap_result = assess_daily_segment(gap).to_dict()
    checks["segment:hidden_gap_rewarm"] = (
        gap_result["status"] == "INSUFFICIENT_HISTORY"
        and gap_result["reason"] == "hidden_session_gap_requires_rewarm"
    )

    text = canonical(design).decode("utf-8")
    checks["authority:not_registered"] = (
        design["authority"]["fact_registration"] is False
        and design["authority"]["semantic_catalog_registration"] is False
        and design["authority"]["market_data_read"] is False
        and design["authority"]["stage_projection"] is False
        and "candidate_generation" in text
    )

    failed = sorted(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": "quanthus.directional-path-envelope-synthetic-result.v1",
        "work_item": "P3-STAGE-012-E5",
        "state": "PASS_SYNTHETIC_QUALIFICATION" if not failed else "FAIL_SYNTHETIC_QUALIFICATION",
        "design_contract_digest": CONTRACT_DIGEST,
        "synthetic_contract_digest": SYNTHETIC_DIGEST,
        "checks": dict(sorted(checks.items())),
        "failed_checks": failed,
        "cases": observed_cases,
        "claim_ceiling": (
            "Structure-owned synthetic qualification only. No Fact registration, "
            "market separability, window optimality, threshold validity, Stage qualification "
            "or economic usefulness is claimed."
        ),
        "authority": {
            "market_data_read": False,
            "runtime_job": False,
            "threshold_search": False,
            "candidate_generation": False,
            "fact_registration": False,
            "semantic_catalog_change": False,
            "existing_b6_path_contract_change": False,
            "preserved_2021_evaluation_read": False,
        },
    }


def main() -> int:
    print(json.dumps(run(), ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
