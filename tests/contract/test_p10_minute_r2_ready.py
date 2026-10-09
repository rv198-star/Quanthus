"""R2 minimum-grade data delivery is the sole current research READY gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.pipelines.p10_minute_readiness import (
    POLICY_ID,
    ready_after_verified_storage,
)


@pytest.fixture
def all_r2() -> dict[str, int]:
    return {"R0": 0, "R1": 0, "R2": 4981, "R3": 0, "SUSPENDED": 11}


def test_policy_document_matches_runtime_id() -> None:
    policy_file = (
        Path(__file__).resolve().parents[2]
        / "config/pipelines/p10-minute-r2-ready.v1.json"
    )
    value = json.loads(policy_file.read_text())
    assert value["policy_id"] == POLICY_ID
    assert value["ready_if"]["requires_extra_per_board_or_R0_R1_threshold"] is False
    assert value["ready_if"]["requires_R2_additional_approval"] is False


def test_oct08_historic_correction_now_ready_despite_80_r2() -> None:
    assert ready_after_verified_storage(
        {"R0": 4865, "R1": 36, "R2": 80, "R3": 0, "SUSPENDED": 11},
        admitted_raw=4981,
    )


def test_all_traded_as_genuine_r2_are_ready(all_r2) -> None:
    assert ready_after_verified_storage(all_r2, admitted_raw=4981)


def test_even_one_remaining_r3_is_not_ready() -> None:
    assert not ready_after_verified_storage(
        {"R0": 4900, "R1": 0, "R2": 80, "R3": 1, "SUSPENDED": 11},
        admitted_raw=4980,
    )


def test_nearly_full_r0_alone_never_outvotes_r3() -> None:
    assert not ready_after_verified_storage(
        {"R0": 4980, "R2": 0, "R3": 1, "SUSPENDED": 11},
        admitted_raw=4980,
    )


@pytest.mark.parametrize("bad", [
    {"R0": 4865, "R1": 36, "R2": 80, "R3": 0, "SUSPENDED": 10},
    {"R0": 4865, "R1": 36, "R2": 80, "R3": 0, "SUSPENDED": 11, "R4": 1},
    {"R0": 4865, "R1": 36, "R2": -1, "R3": 0, "SUSPENDED": 92},
    {"R0": True, "R1": 0, "R2": 4981, "R3": 0, "SUSPENDED": 11},
])
def test_bad_grade_denominators_fail_closed(bad: dict) -> None:
    with pytest.raises(ValueError):
        ready_after_verified_storage(bad, admitted_raw=4981)


def test_missing_source_storage_cannot_claim_ready(all_r2) -> None:
    with pytest.raises(ValueError, match="admitted"):
        ready_after_verified_storage(all_r2, admitted_raw=4980)


def test_legacy_stored_readiness_is_superseded_only_after_physical_validation(
    monkeypatch, tmp_path,
):
    import hashlib
    from datetime import date

    from quanthus.pipelines import p10_minute_delivery_mail as delivery

    root=tmp_path/"old-package"
    package=root/"packages/2026-10-08/fixed"
    package.mkdir(parents=True)
    manifest=package/"manifest.json"
    manifest.write_text('{"kind":"legacy_immutable"}')
    sha="sha256:"+hashlib.sha256(manifest.read_bytes()).hexdigest()
    (package/"SEALED").write_text(sha+"\n")
    receipts=root/"date-status/2026-10-08"
    receipts.mkdir(parents=True)
    original={
        "market_date":"2026-10-08","manifest_sha256":sha,
        "quality_grades":{"R0":4865,"R1":36,"R2":80,"R3":0,"SUSPENDED":11},
        "research_ready":False,
    }
    source=receipts/"fixed.json"
    source.write_text(json.dumps(original))
    def fake_verify(_root):
        assert _root==package
        return {
            "market_date":"2026-10-08",
            "manifest_sha256":sha,
            "quality_grades":original["quality_grades"],
            "admitted_raw":4981,
            "research_ready":False,
        }
    monkeypatch.setattr(delivery,"verify",fake_verify)
    revised=delivery.verified_minute_grade(root,date(2026,10,8))
    assert revised["research_ready"] is True
    assert revised["sealed_legacy_research_ready"] is False
    assert json.loads(source.read_text())==original
