from copy import deepcopy
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan
from quanthus.pipelines.securities_warehouse.base231_native_delta import (
    HOLDING,
    future_claim_frames,
    holder_views,
    schemas,
)
from tests.unit.test_base23_ownership import BODY, project
from tests.unit.test_base231_domain_pack import LIMITS


def compare(tmp_path, old, new):
    with Budget(tmp_path / "job", [], LIMITS) as budget:
        for name, rows in [("old", old), ("new", new)]:
            pq.write_table(pa.Table.from_pylist(rows, schema=schemas()[HOLDING]),
                           budget.root / (name + ".parquet"))
        with GlobalScan(budget) as scan:
            scan.add("old_holding", [budget.root / "old.parquet"])
            scan.add("new_holding", [budget.root / "new.parquet"])
            holder_views(scan)
            result = scan.connection.execute("SELECT * FROM holder_additions").fetch_arrow_table()
            held = scan.connection.execute("SELECT count(*) FROM held_frames").fetchone()[0]
    return result.to_pylist(), held


def row():
    rows, _ = project(BODY)
    return rows[HOLDING][0]


def test_unchanged_nullable_component_reuses_old_version_and_adds_only_new_frame(tmp_path):
    a = row()
    new = dict(a, knowledge_from=datetime(2026, 10, 12, tzinfo=timezone.utc))
    b = dict(new, observation_start=datetime(2026, 9, 3, 16, tzinfo=timezone.utc))
    before = deepcopy([a])
    selected, held = compare(tmp_path, [a], [new, b])
    assert len(selected) == 1 and selected[0]["observation_start"] == b["observation_start"]
    assert held == 0 and before == [a]


def test_one_different_component_holds_complete_frame_without_blocking_other_frames(tmp_path):
    a = row()
    changed = dict(a, holding_quantity=a["holding_quantity"] + 1)
    extra = dict(a, shareholder_source_name="另一个合成股东", holder_rank=2)
    safe = dict(extra, observation_start=datetime(2026, 9, 3, 16, tzinfo=timezone.utc))
    selected, held = compare(tmp_path, [a], [changed, extra, safe])
    assert held == 1 and len(selected) == 1
    assert selected[0]["observation_start"] == safe["observation_start"]


def test_old_superseded_values_do_not_override_latest_native_component(tmp_path):
    a = row()
    latest = dict(a, knowledge_from=datetime(2026, 10, 10, tzinfo=timezone.utc),
                  holding_quantity=a["holding_quantity"] + 1)
    selected, held = compare(tmp_path, [a, latest], [dict(latest)])
    assert selected == [] and held == 0


def test_old_component_absence_does_not_infer_exit_or_allow_double_counting(tmp_path):
    a = row()
    changed_basis = dict(a, holding_percentage_unit="SOURCE_OTHER_RATIO_BASIS")
    selected, held = compare(tmp_path, [a], [changed_basis])
    assert selected == [] and held == 1


def test_complete_compatible_source_prefix_can_add_a_missing_holder(tmp_path):
    a = row()
    b = dict(a, shareholder_source_name="另一个合成股东", holder_rank=2)
    selected, held = compare(tmp_path, [a], [a, b])
    assert len(selected) == 1 and selected[0]["holder_rank"] == 2
    assert held == 0


def test_future_claim_quarantine_uses_original_shanghai_capture_date():
    entry = {"category": "股东研究", "proof": {
        "source_member_sha256": "sha256:" + "a" * 64,
        "literal_identity": {"identity_key": ["provider", "002001", "股东研究"],
                             "actual_acquisition_instant": "2026-09-05T16:00:00+00:00",
                             "content_sha256": "sha256:" + "b" * 64},
        "frames": [{"business_date": "2026-09-06"}, {"business_date": "2026-09-07"}]}}
    held = future_claim_frames([entry])
    assert len(held) == 1
    assert held[0]["observation_start"] == datetime(2026, 9, 6, 16, tzinfo=timezone.utc)
