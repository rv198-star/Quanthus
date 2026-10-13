from copy import deepcopy
from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base231_resume_image import merge_versions


def case():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    old_clock = datetime(2026, 8, 4, tzinfo=timezone.utc)
    new_clock = datetime(2026, 10, 13, tzinfo=timezone.utc)
    resume = datetime(2026, 7, 30, tzinfo=timezone.utc)
    before = [
        {
            "listing_status_id": str(i),
            "listing_id": "L" + str(i),
            "effective_from": start,
            "effective_to": None,
            "knowledge_from": old_clock,
            "knowledge_to": None,
            "status_type": "SUSPENSION",
            "status_value": "SUSPENDED",
            "record_source": "old",
            "source_record_id": str(i),
            "source_revision": "old",
            "source_artifact_sha256": "sha256:" + "b" * 64,
            "reason_code": "unchanged",
        }
        for i in range(4)
    ]
    revisions = []
    for row in before[:3]:
        revisions.extend(
            [
                {**row, "knowledge_to": new_clock},
                {
                    **row,
                    "knowledge_from": new_clock,
                    "effective_to": resume,
                    "record_source": "cninfo.official_issuer_resume",
                    "source_record_id": "notice-" + row["listing_id"],
                    "source_revision": "new",
                    "source_artifact_sha256": "sha256:" + "a" * 64,
                },
            ]
        )
    return before, revisions


def test_exact_pair_merge_preserves_unrelated_rows_and_all_original_atoms():
    before, revisions = case()
    original = deepcopy([before, revisions])
    output = merge_versions(before, revisions)
    assert [before, revisions] == original
    assert len(output) == 7 and before[3] in output
    for row in before[:3]:
        closed = next(
            r
            for r in output
            if r["listing_status_id"] == row["listing_status_id"] and r["knowledge_to"] is not None
        )
        assert {**closed, "knowledge_to": None} == row


@pytest.mark.parametrize(
    "fault",
    [
        "old_value",
        "new_value",
        "new_parent",
        "early_clock",
        "wrong_pair",
        "duplicate",
        "missing",
        "already_closed",
        "new_column",
    ],
)
def test_rehashed_or_unpaired_changes_fail_closed(fault):
    before, revisions = case()
    if fault == "old_value":
        revisions[0]["reason_code"] = "changed"
    elif fault == "new_value":
        revisions[1]["status_value"] = "ACTIVE"
    elif fault == "new_parent":
        revisions[1]["listing_id"] = "wrong"
    elif fault == "early_clock":
        revisions[0]["knowledge_to"] = revisions[0]["knowledge_from"]
    elif fault == "wrong_pair":
        revisions[1]["knowledge_from"] = datetime(2026, 10, 14, tzinfo=timezone.utc)
    elif fault == "duplicate":
        before.append(before[0].copy())
    elif fault == "missing":
        revisions.pop()
    elif fault == "already_closed":
        before[0]["knowledge_to"] = datetime(2026, 10, 12, tzinfo=timezone.utc)
    else:
        revisions[1]["unknown"] = "bad"
    with pytest.raises(BoundedError, match="RESUME_IMAGE_"):
        merge_versions(before, revisions)
