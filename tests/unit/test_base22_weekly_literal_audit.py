"""The new source scope replays complete raw bytes and retains nonempty holds."""

from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_weekly_holder_literals as projector
from quanthus.pipelines.securities_warehouse import base22_weekly_literal_audit as auditor
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, semantic_scan
from tests.unit import test_base22_ownership_audit as predecessor_tests
from tests.unit.test_base22_f10_ownership import HOLDERS, raw, resolve
from tests.unit.test_base22_ownership_audit import seal


def test_literal_ratio_and_explicit_null_are_source_atoms_not_a_domain_pass():
    for token, value in [("1.123456789012 占A股", Decimal("1.123456789012")), ("-", None)]:
        typed, proof = projector.ownership_observation(
            raw(HOLDERS.replace("1.123456789012%", token), "股东研究"),
            source_sha256="sha256:" + "a" * 64,
            resolver=resolve(),
        )
        assert typed[projector.TABLES[1]][0]["holding_percentage"] == value
        assert proof["whole_domain_admission"] == "NOT_GRANTED"
        assert proof["native_history_union"] == "SEPARATE_GATE"
        assert typed[projector.TABLES[1]][0]["raw_holding_percentage"] == token


def test_separate_nonempty_change_rows_never_become_a_silent_category_pass():
    body = (
        HOLDERS.removesuffix("└")
        + "｜变动日期｜股东名称｜持股数｜变动股数｜变动后｜变动后占｜变动类型｜\n"
        + "｜2026-08-01｜合成法人｜1万｜-1万｜0｜0%｜变动｜\n└"
    )
    typed, proof = projector.ownership_observation(
        raw(body, "股东研究"), source_sha256="sha256:" + "a" * 64, resolver=resolve()
    )
    assert len(typed[projector.TABLES[1]]) == 1
    assert proof["separate_nonempty_domains"][0]["qualification"] == "NOT_GRANTED"
    assert proof["separate_nonempty_domains"][0]["nonempty_data_rows"] == 1


@pytest.mark.parametrize(
    "defect", [None, "atom", "extra-empty", "raw-repacked", "separate-disposition"]
)
def test_full_new_source_scope_replays_typed_shards_even_after_all_hashes_are_repacked(
    tmp_path, monkeypatch, defect
):
    # Reuse the existing complete, deterministic four-week synthetic factory;
    # only the explicit projector/profile changes. The old factory is restored.
    with monkeypatch.context() as m:
        m.setattr(predecessor_tests, "ownership_observation", projector.ownership_observation)
        m.setattr(predecessor_tests, "CONTRACT_ID", projector.CONTRACT_ID)
        body = predecessor_tests.image(tmp_path / "image")
    root = tmp_path / "image"
    body["purpose"] = "WEEKLY_F10_LITERAL_SOURCE_DERIVED_ENTITY_SCOPE_NOT_COMPLETE_UNION"
    body["projector_sha256"] = file_sha(Path(projector.__file__))
    body["replay_evaluator_sha256"] = file_sha(Path(auditor.__file__))
    body["unqualified_separate_domain_rows"] = {}
    if defect in {"atom", "extra-empty"}:
        item = next(i for i in body["outputs"] if i["table"] == projector.TABLES[1])
        table = pq.ParquetFile(root / item["path"]).read()
        if defect == "atom":
            rows = table.to_pylist()
            rows[0]["holding_percentage"] += 1
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), root / item["path"])
            item["semantic_scan"] = semantic_scan([root / item["path"]], table.schema)
        else:
            p = root / "extra.parquet"
            pq.write_table(pa.Table.from_pylist([], schema=table.schema), p)
            body["outputs"].append(
                {**item, "path": p.name, "partition": "extra", "number": 0, "rows": 0}
            )
    elif defect == "raw-repacked":
        member = body["raw_local_members"][0]
        p = root / member["path"]
        table = pq.ParquetFile(p).read()
        rows = table.to_pylist()
        rows[1] = raw(HOLDERS.replace("1.234567万", "2.345678万"), "股东研究")
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), p)
        member["sha256"] = file_sha(p)
        body["source_members"][0]["member"]["sha256"] = file_sha(p)
    elif defect == "separate-disposition":
        body["unqualified_separate_domain_rows"] = {"dwd_shareholding_change": 1}
    seal(root, body)
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "audit", [root], limits) as budget:
        if defect is None:
            result = auditor.audit_weekly_literal_observations(root, body, budget)
            assert result["raw_rows"] == 8
            assert result["complete_Base22"] is False
            assert result["unqualified_separate_domain_rows"] == {}
        else:
            with pytest.raises(RuntimeError):
                auditor.audit_weekly_literal_observations(root, body, budget)
