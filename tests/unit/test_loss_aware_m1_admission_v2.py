"""Owner 2026-10-09 admission boundary: R2 stored and flagged; R3 no OHLC/raw."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from quanthus.pipelines.loss_aware_m1_admission_v2 import (
    _advance_research_pointer,
    _research_ready,
    _status_record,
    _validate_r2_native,
    route_grade,
    verify_v2,
)

DAY = "2026-09-24"


@pytest.mark.parametrize(
    ("grade", "stored", "research_admitted"),
    [
        ("R0", True, True),
        ("R1", True, True),
        ("R2", True, False),
        ("R3", False, False),
        ("SUSPENDED", False, False),
    ],
)
def test_grade_storage_vs_research_are_separate(
    grade: str, stored: bool, research_admitted: bool
) -> None:
    assert route_grade(grade) == (stored, research_admitted)


def test_unrecognized_grade_does_not_gain_warehouse_access() -> None:
    with pytest.raises(ValueError, match="unknown"):
        route_grade("R4")


def _disposition(grade: str) -> dict:
    return {
        "stock_code": "000001",
        "market_date": DAY,
        "quality_grade": grade,
        "reason": "missing_in_critical_session_window",
        "missing_actual_minutes": ["202609241023"],
        "critical_gap": True,
        "auction_source_observed": True,
        "source_raw_sha256": "a" * 64,
        "source_acquired_at_or_after": "2026-09-28",
        "source_semantic_disagreement": "not_independently_compared",
        "amount_quality": "source_absent_null_not_estimated",
    }


def test_r2_real_data_enters_with_all_quality_restrictions(tmp_path: Path) -> None:
    quality = _status_record(_disposition("R2"), tmp_path)
    assert quality["formally_ingested"] is True
    assert quality["default_research_admitted"] is False
    assert quality["quality_reason"] == "missing_in_critical_session_window"
    assert quality["missing_actual_minutes"] == ["202609241023"]
    assert quality["critical_gap"] is True
    assert quality["raw_sha256"] == "a" * 64
    assert quality["allowed_consumer"] == "explicit_r2_review_only"


def test_r3_stays_forensic_metadata_without_warehouse_admission(tmp_path: Path) -> None:
    quality = _status_record(_disposition("R3"), tmp_path)
    assert quality["formally_ingested"] is False
    assert quality["default_research_admitted"] is False
    assert quality["allowed_consumer"] == "none"
    assert quality["quarantine_evidence_ref"] == str(tmp_path)


def test_no_untraced_r2_allowed(tmp_path: Path) -> None:
    old = _disposition("R2")
    old["source_raw_sha256"] = None
    with pytest.raises(ValueError, match="source SHA"):
        _status_record(old, tmp_path)


def _single_sparse_bar_source() -> bytes:
    from quanthus.providers.loss_aware_m1 import _grid

    stamps = ["202609240930", *_grid(DAY)]
    raw = [
        [stamp, "11", "11", "11", "11", "1", {}]
        for stamp in stamps
        if stamp != "202609241023"
    ]
    return json.dumps({"code": 0, "data": {"sz000001": {"m1": raw}}}).encode()


def test_r2_actual_native_rows_are_retained_without_fabrication() -> None:
    raw = _single_sparse_bar_source()
    ledger = {
        **_disposition("R2"),
        "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_native_rows": 240,
    }
    native = _validate_r2_native(
        raw, code="000001", day=DAY,
        daily_shares=24000, daily_close=11,
        disposition=ledger,
    )
    assert len(native) == 240
    assert "202609241023" not in dict(native)
    assert "202609240930" in dict(native)


def test_r2_bad_day_volume_cannot_be_downlabelled_from_r3() -> None:
    raw = _single_sparse_bar_source()
    ledger = {
        **_disposition("R2"),
        "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_native_rows": 240,
    }
    with pytest.raises(ValueError, match="share volume"):
        _validate_r2_native(
            raw, code="000001", day=DAY,
            daily_shares=24001, daily_close=11, disposition=ledger,
        )


def test_research_coverage_counts_only_r0_r1_not_ingested_r2() -> None:
    policy = {
        "thresholds": {
            "min_traded_research_fraction": "0.99",
            "min_board_research_fraction": "0.98",
        }
    }
    states = {}
    for prefix in ("688", "300", "600", "000"):
        for n in range(100):
            code = prefix + f"{n:03}"
            states[code] = {"quality_grade": "R0"}
    for prefix in ("688", "300", "600", "000"):
        states[prefix + "000"]["quality_grade"] = "R2"
    assert _research_ready(states, policy)
    states["300001"]["quality_grade"] = "R2"
    states["300002"]["quality_grade"] = "R2"
    assert not _research_ready(states, policy)


def _synthetic_v2_fixture(root: Path) -> Path:
    """Small physical bar data, full 4992-securities grade denominator."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir()
    raw_dir = root / "ods" / "raw"
    raw_dir.mkdir(parents=True)
    rows = []
    paths = []
    statuses = []
    for i in range(4992):
        code = f"{i:06}"
        grade = ("R0" if i == 0 else "R2" if i == 1 else
                 "R3" if i == 2 else "SUSPENDED")
        formal, research = route_grade(grade)
        raw_sha = None
        if formal:
            raw = f"genuine-supplier-{code}".encode()
            path = raw_dir / f"{code}.json.gz"
            path.write_bytes(gzip.compress(raw))
            raw_sha = hashlib.sha256(raw).hexdigest()
            paths.append(path)
            rows.append(
                {
                    "stock_code": code,
                    "quality_grade": grade,
                    "market_date": DAY,
                    "original_source_observed": True,
                    "formally_ingested": True,
                    "default_research_admitted": research,
                    "quality_reason": "native" if grade == "R0" else "critical_gap",
                    "quality_missing_actual_minutes_json": (
                        "[]" if grade == "R0" else '["202609241023"]'
                    ),
                    "raw_sha256": raw_sha,
                }
            )
        statuses.append(
            {
                "stock_code": code,
                "quality_grade": grade,
                "market_date": DAY,
                "quality_reason": (
                    "native" if grade == "R0" else
                    "critical_gap" if grade == "R2" else
                    "rejected" if grade == "R3" else "suspended"
                ),
                "missing_actual_minutes": (
                    ["202609241023"] if grade == "R2" else []
                ),
                "raw_sha256": raw_sha,
                "formally_ingested": formal,
                "default_research_admitted": research,
            }
        )
    quality = root / "quality-status.jsonl"
    quality.write_bytes(b"".join(
        (json.dumps(x, sort_keys=True) + "\n").encode() for x in statuses
    ))
    paths.append(quality)
    bars = root / "dwd_security_bar_1m_loss_aware_v2.parquet"
    pq.write_table(pa.Table.from_pylist(rows), bars)
    paths.append(bars)
    manifest = {
        "kind": "p10_1m_r2_stored_r3_excluded_v2",
        "product_scope": "intraday-1m-loss-aware-v2",
        "market_date": DAY,
        "total_securities": 4992,
        "admitted_raw_count": 2,
        "observed_rows": 2,
        "rows_per_grade": {"R0": 1, "R2": 1},
        "research_ready": False,
        "files": [
            {
                "path": p.relative_to(root).as_posix(),
                "bytes": p.stat().st_size,
                "sha256": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in paths
        ],
    }
    m = root / "manifest.json"
    m.write_text(json.dumps(manifest) + "\n")
    (root / "SEALED").write_text(
        "sha256:" + hashlib.sha256(m.read_bytes()).hexdigest() + "\n"
    )
    return root


def test_v2_physical_storage_has_r2_bars_and_no_r3_source(tmp_path: Path) -> None:
    root = _synthetic_v2_fixture(tmp_path / "product")
    accepted = verify_v2(root)
    assert accepted["admitted_raw_count"] == 2
    assert accepted["grade_rows"] == {"R0": 1, "R2": 1}
    assert not (root / "ods" / "raw" / "000002.json.gz").exists()


def test_v2_fails_if_r3_raw_is_injected_even_if_grade_reports_reject(
    tmp_path: Path,
) -> None:
    root = _synthetic_v2_fixture(tmp_path / "product")
    (root / "ods" / "raw" / "000002.json.gz").write_bytes(b"forbidden")
    with pytest.raises(ValueError):
        verify_v2(root)


def test_v2_fails_if_r2_actual_source_raw_missing(tmp_path: Path) -> None:
    root = _synthetic_v2_fixture(tmp_path / "product")
    (root / "ods" / "raw" / "000001.json.gz").unlink()
    with pytest.raises(ValueError):
        verify_v2(root)


def _receipt(day: str, digest: str = "sha256:foo", *, ready: bool = True) -> dict:
    return {
        "market_date": day,
        "manifest_sha256": digest,
        "research_ready": ready,
    }


def test_v2_replay_repairs_missing_pointer_and_does_not_regress(tmp_path: Path) -> None:
    original = _receipt("2026-09-24")
    assert _advance_research_pointer(tmp_path, original)
    pointer = tmp_path / "CURRENT_RESEARCH_1M_V2.json"
    first_bytes = pointer.read_bytes()
    assert not _advance_research_pointer(tmp_path, original)
    assert pointer.read_bytes() == first_bytes
    # A later sealed date may advance the consumer, but recovering an earlier
    # already-accepted package cannot move it back in time.
    later = _receipt("2026-09-29", digest="sha256:bar")
    assert _advance_research_pointer(tmp_path, later)
    advanced_bytes = pointer.read_bytes()
    assert not _advance_research_pointer(tmp_path, original)
    assert pointer.read_bytes() == advanced_bytes


def test_v2_rejects_same_date_conflicting_manifest(tmp_path: Path) -> None:
    assert _advance_research_pointer(tmp_path, _receipt("2026-09-29"))
    with pytest.raises(ValueError, match="same-date digest conflict"):
        _advance_research_pointer(
            tmp_path, _receipt("2026-09-29", digest="sha256:different")
        )


def test_v2_nonready_day_never_advances_research_current(tmp_path: Path) -> None:
    before = _receipt("2026-09-30")
    assert _advance_research_pointer(tmp_path, before)
    pointer = tmp_path / "CURRENT_RESEARCH_1M_V2.json"
    old = pointer.read_bytes()
    assert not _advance_research_pointer(
        tmp_path, _receipt("2026-10-08", ready=False)
    )
    assert pointer.read_bytes() == old
