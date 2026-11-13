from copy import deepcopy
from datetime import date, timedelta

import pytest

from quanthus.r9_source_first import GROUPS, identity_order, select_roles


def candidates():
    rows = []
    for role, anchor, _, pool_count in GROUPS:
        group = []
        for _ in range(pool_count):
            i = len(rows) + len(group)
            end = date.fromisoformat(anchor)
            group.append(
                {
                    "company_id": f"c{i}",
                    "security_id": f"s{i}",
                    "listing_id": f"l{i}",
                    "sample_id": f"sample{i}",
                    "episode_id": f"episode{i}",
                    "role": role,
                    "anchor": anchor,
                    "eligibility_reasons": [],
                    "sessions": [
                        {"date": str(end - timedelta(days=39 - j)), "session_id": f"{i}/{j}"}
                        for j in range(40)
                    ],
                }
            )
        rows.extend(sorted(group, key=identity_order))
    return rows


def test_metadata_rejections_are_accounted_and_hash_order_is_unchanged():
    rows = candidates()
    rows[0]["eligibility_reasons"] = ["implemented_action_or_unknown_date"]
    before = deepcopy(rows)
    result = select_roles(rows)
    assert len(result["rows"]) == 64
    assert result["rows"][0]["company_id"] == rows[1]["company_id"]
    assert rows == before
    assert all("eligibility_reasons" not in r for r in result["rows"])


def test_capacity_failure_cannot_borrow_from_another_group():
    rows = candidates()
    for row in rows[96:121]:
        row["eligibility_reasons"] = ["state_unavailable"]
    with pytest.raises(ValueError, match="SOURCE_CAPACITY:guard"):
        select_roles(rows)


@pytest.mark.parametrize("change", ["drop", "duplicate", "reorder", "role"])
def test_fixed_pool_and_role_assignment_fail_closed(change):
    rows = candidates()
    if change == "drop":
        rows.pop()
    elif change == "duplicate":
        rows[-1]["company_id"] = rows[0]["company_id"]
    elif change == "reorder":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        rows[96]["role"] = "update"
    with pytest.raises(ValueError):
        select_roles(rows)
