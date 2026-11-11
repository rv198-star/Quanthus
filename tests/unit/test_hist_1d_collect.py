from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from quanthus.pipelines.hist_1d_collect import capture, select_tasks


def test_capture_preserves_lexical_values_and_reuses_verified_bytes(tmp_path: Path) -> None:
    class Response:
        error_code = "0"
        error_msg = "success"
        fields = ["date", "code", "open"]
        remaining = True

        def next(self):
            result, self.remaining = self.remaining, False
            return result

        def get_row_data(self):
            return ["2005-01-04", "sh.600601", "0.1000000001"]

    class Source:
        calls = 0

        def query_history_k_data_plus(self, **args):
            self.calls += 1
            return Response()

    source = Source()
    task = {
        "listing_id": "listing",
        "security_id": "security",
        "code": "sh.600601",
        "start_date": "2005-01-01",
        "end_date": "2005-01-31",
    }
    path = tmp_path / "daily.json.gz"
    first = capture(source, task, "daily", path, "fixed-plan")
    assert first["rows"] == 1
    assert json.loads(gzip.decompress(path.read_bytes()))["rows"][0][2] == "0.1000000001"
    assert capture(source, task, "daily", path, "fixed-plan")["reused"]
    assert source.calls == 1
    with pytest.raises(ValueError, match="CAPTURE_CHECKPOINT_IDENTITY_MISMATCH"):
        capture(source, task, "daily", path, "changed-plan")
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="CAPTURE_CHECKPOINT_IDENTITY_MISMATCH"):
        capture(source, task, "daily", path, "fixed-plan")


def test_shards_are_disjoint_and_cover_exact_frozen_prefix() -> None:
    tasks = [{"id": i} for i in range(20)]
    selected = [select_tasks(tasks, 17, shard, 4) for shard in range(4)]
    assert sorted(t["id"] for group in selected for t in group) == list(range(17))
    with pytest.raises(ValueError, match="COLLECTION_LIMIT_OUT_OF_SCOPE"):
        select_tasks(tasks, 21, 0, 4)
    with pytest.raises(ValueError, match="COLLECTION_LIMIT_OUT_OF_SCOPE"):
        select_tasks(tasks, 17, 0, 5)
