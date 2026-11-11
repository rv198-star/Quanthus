from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.daily_copy_budget import CopyReservation
from quanthus.pipelines.securities_warehouse.standard_daily import copy_file


def test_copy_batch_checks_full_quota_before_creating_members(tmp_path: Path):
    source = tmp_path / "source"
    source.write_bytes(b"x" * 2_000_000)
    with Budget(
        tmp_path / "job", [source], Limits(allocated_bytes=1_000_000, free_floor_bytes=1)
    ) as b:
        target = b.root / "copy"
        with pytest.raises(BoundedError, match="ALLOCATED_WRITE_BUDGET_EXCEEDED"):
            CopyReservation(b, {target: source.stat().st_size})
        assert not target.exists()


def test_batch_preserves_each_file_without_one_tree_scan_per_member(tmp_path: Path):
    source = tmp_path / "source"
    source.write_bytes(b"original bytes")
    with Budget(tmp_path / "job", [source], Limits(free_floor_bytes=1)) as b:
        original_check = b.check
        scans = []

        def counted_check(**kwargs):
            scans.append(kwargs)
            return original_check(**kwargs)

        b.check = counted_check
        targets = {b.root / "copies" / str(i): source.stat().st_size for i in range(100)}
        batch = CopyReservation(b, targets)
        for target in targets:
            copy_file(source, target, batch)
            assert target.read_bytes() == source.read_bytes()
            assert target.stat().st_nlink == 1
        batch.finish()
        assert len(scans) < 10
        with pytest.raises(BoundedError, match="UNRESERVED_DESTINATION"):
            batch.destination(b.root / "unexpected")
