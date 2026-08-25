from __future__ import annotations

import pytest

from quanthus.event_library.sharded_batch import feature_panel_partition_count


def test_feature_panel_keeps_two_logical_parts_for_one_or_two_workers() -> None:
    assert feature_panel_partition_count(1) == 2
    assert feature_panel_partition_count(2) == 2
    assert feature_panel_partition_count(3) == 3
    assert feature_panel_partition_count(4) == 4


def test_feature_panel_rejects_invalid_worker_target() -> None:
    with pytest.raises(ValueError, match="Worker target is invalid"):
        feature_panel_partition_count(0)
