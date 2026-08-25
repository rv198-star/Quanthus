from __future__ import annotations

import pytest

from quanthus.event_library.sharded_batch import _schedule_analysis_payloads


def test_analysis_schedule_overlaps_panels_with_candidate_scans() -> None:
    panels = tuple(
        {"kind": "feature-panel", "id": name} for name in ("label", "report")
    )
    scans = tuple(
        {"kind": "candidate-scan", "id": f"scan-{index}"} for index in range(6)
    )

    scheduled = _schedule_analysis_payloads(panels, scans)

    assert [payload["id"] for payload in scheduled] == [
        "label",
        "scan-0",
        "scan-1",
        "scan-2",
        "report",
        "scan-3",
        "scan-4",
        "scan-5",
    ]
    assert {id(payload) for payload in scheduled} == {
        id(payload) for payload in (*panels, *scans)
    }


def test_analysis_schedule_rejects_unproven_topology() -> None:
    panel = {"kind": "feature-panel"}
    scan = {"kind": "candidate-scan"}

    with pytest.raises(RuntimeError, match="topology differs"):
        _schedule_analysis_payloads((panel,), (scan,))
    with pytest.raises(RuntimeError, match="topology differs"):
        _schedule_analysis_payloads((panel, panel), (scan,) * 5)
