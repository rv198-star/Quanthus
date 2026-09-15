from __future__ import annotations

from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.stage_learning import realized_identity_admission as subject

ROOT = find_project_root()


def _token(identity: str, cell: str, window_id: str, start: str) -> dict[str, object]:
    era, board = cell.split("/", 1)
    return {
        "security_id": identity,
        "security_code": identity,
        "listing_id": f"listing-{identity}",
        "era": era,
        "board_family": board,
        "board_code": board,
        "venue_id": "venue",
        "window_id": window_id,
        "window_start": start,
        "window_end": "2020-12-31",
        "open_session_count": 61,
        "exposure_level": "L2A_TECHNICAL_INPUT_ADMISSION_READ",
    }


def test_p023_contract_is_primary_first_and_semantic_blind() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    primary = config["primary_contract"]
    fallback = config["fallback_reservoir"]
    admission = config["admission_contract"]
    gate = config["realized_plan_gate"]
    assert primary["row_count"] == 192
    assert primary["primary_per_cell"] == 24
    assert primary["global_identity_unique"] is True
    assert primary["read_all_primaries_before_fallback_activation"] is True
    assert primary["primary_failure_consumes_realized_identity"] is False
    assert fallback["maximum_candidate_identities_per_cell"] == 256
    assert fallback["maximum_windows_per_identity"] == 4
    assert fallback["membership_and_order_frozen_before_input_read"] is True
    assert fallback["planned_primary_identity_may_appear_in_other_fallback_windows"] is True
    assert fallback["realized_primary_identity_blocks_fallback_activation_globally"] is True
    assert fallback["cross_cell_fill"] is False
    assert admission["fallback_trigger"] == "CELL_TECHNICAL_READINESS_DEFICIT_ONLY"
    assert admission["skip_fallback_without_market_read_if_identity_already_realized"] is True
    assert gate["required_rows_per_cell"] == 24
    assert gate["required_total_rows"] == 192
    assert gate["global_identity_unique"] is True
    assert gate["pass_allows_only"] == "SEPARATE_P020_C_PREREGISTRATION"
    authority = config["authority"]
    assert authority["input_readiness_market_read"] is True
    for key in (
        "raw_ohlc_publish_or_retain",
        "structure_fact_build",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "p020_c_semantic_review",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_fallback_reservoir_can_reuse_planned_identity_on_different_exact_window() -> None:
    cell = "ERA/STAR"
    primary = {
        "security_id": "A",
        "security_code": "A",
        "listing_id": "listing-A",
        "era": "ERA",
        "board_family": "STAR",
        "board_code": "STAR",
        "venue_id": "venue",
        "cell": cell,
        "cell_rank": 1,
        "slot": 1,
        "role": "primary",
        "window_id": "primary-window",
        "window_start": "2020-01-01",
        "window_end": "2020-03-31",
        "prior_exposure_level": "L2A_TECHNICAL_INPUT_ADMISSION_READ",
    }
    tokens = [
        _token("A", cell, "primary-window", "2020-01-01"),
        _token("A", cell, "alternate-window", "2020-04-01"),
        _token("B", cell, "b-window", "2020-01-01"),
    ]
    contract = {
        "cell_order": [cell],
        "maximum_candidate_identities_per_cell": 256,
        "maximum_windows_per_identity": 4,
        "identity_seed": "identity",
        "window_seed": "window",
    }
    rows = subject._freeze_fallback_reservoir(
        tokens,
        consumed=set(),
        primary_rows=[primary],
        contract=contract,
    )
    keys = {(row["security_id"], row["window_id"]) for row in rows}
    assert ("A", "primary-window") not in keys
    assert ("A", "alternate-window") in keys
    assert ("B", "b-window") in keys


def test_fallback_identity_conflict_skips_without_market_read(monkeypatch) -> None:
    cell = "ERA/STAR"
    reservoir = [
        {
            **subject._admission_candidate(
                _token("already-realized", cell, "conflict", "2020-01-01"),
                identity_rank=1,
                window_rank=1,
            ),
            "cell_order": 1,
        },
        {
            **subject._admission_candidate(
                _token("replacement", cell, "replacement", "2020-04-01"),
                identity_rank=2,
                window_rank=1,
            ),
            "cell_order": 1,
        },
    ]
    calls: list[str] = []

    def fake_screen_candidates(**kwargs):
        candidate = kwargs["candidates"][0]
        calls.append(str(candidate["security_id"]))
        return [
            {
                "security_id": candidate["security_id"],
                "security_code": candidate["security_code"],
                "era": candidate["era"],
                "board_family": candidate["board_family"],
                "cell": candidate["cell"],
                "window_id": candidate["window_id"],
                "window_start": candidate["window_start"],
                "window_end": candidate["window_end"],
                "identity_rank": candidate["identity_rank"],
                "window_rank": candidate["window_rank"],
                "qualified": True,
                "reason": "61_SESSION_CONTIGUOUS_INPUT_READY",
                "source_binding_digest": "sha256:" + "1" * 64,
            }
        ], [{"window_id": candidate["window_id"]}]

    monkeypatch.setattr(subject, "_screen_candidates", fake_screen_candidates)
    realized = [
        {
            "security_id": "already-realized",
            "cell": "OTHER/CELL",
            "role": "primary",
        }
    ]
    realized_ids = {"already-realized"}
    attempts, activated, receipts = subject._activate_fallbacks(
        reservoir=reservoir,
        realized=realized,
        realized_ids=realized_ids,
        deficits={cell: 1},
        training_root=Path("/tmp/training"),
        node={},
        spec_digest="sha256:" + "2" * 64,
        cell_order=(cell,),
    )
    assert calls == ["replacement"]
    assert attempts[0]["reason"] == "SKIPPED_REALIZED_IDENTITY_CONFLICT"
    assert attempts[0]["market_read"] is False
    assert attempts[1]["activated"] is True
    assert activated[0]["security_id"] == "replacement"
    assert realized_ids == {"already-realized", "replacement"}
    assert receipts == [{"window_id": "replacement"}]
