from __future__ import annotations

from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.stage_learning import clustered_identity_admission as subject

ROOT = find_project_root()
CELL = "STAR_PRE_CHINEXT_20PCT/STAR"


def _candidate(
    identity: str,
    window_id: str,
    start: str,
    end: str,
    *,
    identity_rank: int,
    window_rank: int,
) -> dict[str, object]:
    return {
        "security_id": identity,
        "security_code": identity,
        "listing_id": f"listing-{identity}",
        "era": "STAR_PRE_CHINEXT_20PCT",
        "board_family": "STAR",
        "board_code": "STAR",
        "venue_id": "SSE",
        "cell": CELL,
        "window_id": window_id,
        "window_start": start,
        "window_end": end,
        "prior_exposure_level": "L2A_TECHNICAL_INPUT_ADMISSION_READ",
        "cell_order": 1,
        "identity_rank": identity_rank,
        "window_rank": window_rank,
    }


def _realized(
    identity: str,
    cell: str,
    window_id: str,
    start: str,
    end: str,
) -> dict[str, object]:
    era, board = cell.split("/", 1)
    return {
        "security_id": identity,
        "security_code": identity,
        "listing_id": f"listing-{identity}",
        "era": era,
        "board_family": board,
        "board_code": board,
        "venue_id": "venue",
        "cell": cell,
        "window_id": window_id,
        "window_start": start,
        "window_end": end,
        "role": "primary",
        "admission_reason": "61_SESSION_CONTIGUOUS_INPUT_READY",
        "admission_source_binding_digest": "sha256:" + "1" * 64,
    }


def test_p024_contract_preserves_cluster_accounting_and_semantic_blindness() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    supplement = config["supplement_contract"]
    cluster = config["cluster_contract"]
    assert supplement["deficit_cell"] == CELL
    assert supplement["starting_cell_count"] == 11
    assert supplement["target_cell_count"] == 24
    assert supplement["eligible_source"] == (
        "P023_FALLBACK_ATTEMPT_SKIPPED_REALIZED_IDENTITY_CONFLICT_ONLY"
    )
    assert supplement["completed_p023_readiness_must_not_rerun"] is True
    assert supplement["within_cell_identity_unique"] is True
    assert supplement["cross_cell_repeat_requires_non_overlapping_exact_windows"] is True
    assert cluster["row_count_must_be_reported"] is True
    assert cluster["unique_cluster_count_must_be_reported"] is True
    assert cluster["independent_identity_evidence_count_equals_unique_cluster_count"] is True
    assert cluster["repeated_rows_must_not_inflate_independent_evidence"] is True
    assert config["failure_policy"]["automatic_p025_resource_repair"] is False
    authority = config["authority"]
    assert (
        authority["input_readiness_market_read_for_previously_conflict_skipped_candidates"] is True
    )
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


def test_eligible_candidates_are_only_unread_p023_identity_conflicts() -> None:
    reservoir = [
        _candidate("A", "a1", "2019-08-01", "2019-10-30", identity_rank=2, window_rank=1),
        _candidate("B", "b1", "2019-08-01", "2019-10-30", identity_rank=1, window_rank=1),
        _candidate("C", "c1", "2019-08-01", "2019-10-30", identity_rank=3, window_rank=1),
    ]
    attempts = [
        {
            "cell": CELL,
            "security_id": "A",
            "window_id": "a1",
            "market_read": False,
            "qualified": False,
            "activated": False,
            "reason": "SKIPPED_REALIZED_IDENTITY_CONFLICT",
            "source_binding_digest": None,
        },
        {
            "cell": CELL,
            "security_id": "B",
            "window_id": "b1",
            "market_read": True,
            "qualified": False,
            "activated": False,
            "reason": "NOT_READY",
            "source_binding_digest": "sha256:" + "2" * 64,
        },
        {
            "cell": CELL,
            "security_id": "C",
            "window_id": "c1",
            "market_read": False,
            "qualified": False,
            "activated": False,
            "reason": "SKIPPED_REALIZED_IDENTITY_CONFLICT",
            "source_binding_digest": None,
        },
    ]
    selected = subject._eligible_candidates(
        reservoir_rows=reservoir,
        fallback_attempts=attempts,
        deficit_cell=CELL,
    )
    assert [(row["security_id"], row["window_id"]) for row in selected] == [
        ("A", "a1"),
        ("C", "c1"),
    ]
    assert all(row["p023_market_read"] is False for row in selected)


def test_supplement_skips_overlap_without_read_and_activates_nonoverlap(monkeypatch) -> None:
    existing = [
        _realized(
            "A",
            "STAR_AND_CHINEXT_20PCT/STAR",
            "later-a",
            "2020-08-24",
            "2020-12-31",
        )
    ]
    candidates = [
        {
            **_candidate(
                "A",
                "overlap-a",
                "2020-07-01",
                "2020-09-30",
                identity_rank=1,
                window_rank=1,
            ),
            "p023_reason": "SKIPPED_REALIZED_IDENTITY_CONFLICT",
            "p023_market_read": False,
        },
        {
            **_candidate(
                "A",
                "early-a",
                "2019-08-01",
                "2019-10-30",
                identity_rank=1,
                window_rank=2,
            ),
            "p023_reason": "SKIPPED_REALIZED_IDENTITY_CONFLICT",
            "p023_market_read": False,
        },
    ]
    reads: list[str] = []

    def fake_screen_candidates(**kwargs):
        candidate = kwargs["candidates"][0]
        reads.append(str(candidate["window_id"]))
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
                "source_binding_digest": "sha256:" + "3" * 64,
            }
        ], [{"window_id": candidate["window_id"]}]

    monkeypatch.setattr(subject, "_screen_candidates", fake_screen_candidates)
    attempts, activated, receipts, realized = subject._supplement(
        candidates=candidates,
        realized_rows=existing,
        target_cell=CELL,
        target_count=1,
        training_root=Path("/tmp/training"),
        node={},
        spec_digest="sha256:" + "4" * 64,
    )
    assert reads == ["early-a"]
    assert attempts[0]["reason"] == "SKIPPED_CLUSTER_WINDOW_OVERLAP"
    assert attempts[0]["market_read"] is False
    assert attempts[1]["activated"] is True
    assert activated[0]["security_id"] == "A"
    assert receipts == [{"window_id": "early-a"}]
    summary = subject._cluster_summary(realized)
    assert summary["unique_cluster_count"] == 1
    assert summary["repeated_identity_cluster_count"] == 1
    assert summary["independent_identity_evidence_count"] == 1
    assert len(realized) == 2


def test_cluster_summary_rejects_overlapping_repeated_identity_windows() -> None:
    rows = [
        _realized("A", "ERA1/STAR", "one", "2020-01-01", "2020-03-31"),
        _realized("A", "ERA2/STAR", "two", "2020-03-01", "2020-05-31"),
    ]
    try:
        subject._cluster_summary(rows)
    except subject.ClusteredIdentityAdmissionError as exc:
        assert str(exc) == "cluster_window_overlap"
    else:  # pragma: no cover
        raise AssertionError("overlapping repeated-identity windows must fail closed")
