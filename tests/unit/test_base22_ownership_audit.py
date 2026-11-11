"""Synthetic raw replay with repacked tampering and bounded partition buffers."""

import json
import shutil
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_ownership_audit as a
from quanthus.pipelines.securities_warehouse.base22 import (
    canonical,
    digest,
    file_sha,
    load_contract,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    semantic_scan,
)
from quanthus.pipelines.securities_warehouse.base22_f10_ownership import (
    CONTRACT_ID,
    TABLES,
    ownership_observation,
)
from quanthus.pipelines.securities_warehouse.base22_sources import _tree
from tests.unit.test_base22_f10_ownership import HOLDERS, project, raw
from tests.unit.test_base22_independent_image import _row


def seal(root, body):
    body["physical_members"] = {
        p: {"sha256": file_sha(root / p), "size_bytes": (root / p).stat().st_size}
        for p in _tree(root)
        if p != "QUALIFICATION.json"
    }
    body.pop("identity", None)
    body["identity"] = digest(body)
    (root / "QUALIFICATION.json").write_bytes(canonical(body) + b"\n")


def image(root):
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    root.mkdir()
    shutil.copytree("config/pipelines", root / "contract")
    body = {
        "purpose": "WEEKLY_F10_OWNERSHIP_SOURCE_DERIVED_ENTITY_SCOPE_NOT_COMPLETE_UNION",
        "contract_id": CONTRACT_ID,
        "projector_sha256": file_sha(Path(a.__file__).with_name("base22_f10_ownership.py")),
        "replay_evaluator_sha256": file_sha(Path(a.__file__)),
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "complete_Base22": False,
        "source_members": [],
        "raw_local_members": [],
        "outputs": [],
        "parents": {},
        "raw_row_accounting": "accounting.jsonl",
        "held_raw_categories": {},
        "held_dated_observations": {},
    }
    for table in ("dwd_security", "dwd_security_identifier"):
        row = _row(ss[table])
        row.update(
            security_id="security",
            effective_from=datetime(2010, 1, 1, tzinfo=timezone.utc),
            knowledge_from=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        if table == "dwd_security":
            row["company_id"] = "company"
        else:
            row.update(identifier_namespace="CN_STOCK_CODE:SZSE", identifier_value="002001")
        rel = table + ".parquet"
        pq.write_table(pa.Table.from_pylist([row], schema=ss[table]), root / rel)
        body["parents"][table] = [{"path": rel, "rows": 1}]
    rawcounts, successful, scalars = Counter(), Counter(), Counter()

    def write(table, part, number, rows):
        rel = f"payload/{table}/{part}/{number}.parquet"
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), p)
        body["outputs"].append(
            {
                "table": table,
                "partition": part,
                "number": number,
                "path": rel,
                "rows": len(rows),
                "semantic_scan": semantic_scan([p], ss[table]),
            }
        )

    sink = a.OwnershipReplaySink(contract["tables"], write)
    lines = []
    for day in ("2026-08-01", "2026-08-08", "2026-08-22", "2026-08-29"):
        rr = [raw(), raw(HOLDERS, "股东研究")]
        for r in rr:
            r["acquired_at"] = day + "T08:00:03.123456+08:00"
        rel = day + ".parquet"
        pq.write_table(pa.Table.from_pylist(rr), root / rel)
        sha = file_sha(root / rel)
        body["source_members"].append(
            {"day": day, "root": "/sources/removed-and-inaccessible", "member": {"sha256": sha}}
        )
        body["raw_local_members"].append({"source_day": day, "path": rel, "sha256": sha, "rows": 2})
        from quanthus.pipelines.securities_warehouse.financial_reporting import (
            financial_company_resolver_from_listed_company_rows,
        )

        resolver = financial_company_resolver_from_listed_company_rows(
            pq.read_table(root / "dwd_security.parquet").to_pylist(),
            pq.read_table(root / "dwd_security_identifier.parquet").to_pylist(),
        )
        for r in rr:
            typed, ev = ownership_observation(r, source_sha256=sha, resolver=resolver)
            sink.add(typed)
            rawcounts[r["category"]] += 1
            successful[r["category"]] += 1
            scalars.update(ev["scalar_classes"])
            lines.append(
                {
                    "source_day": day,
                    "stock_code": r["stock_code"],
                    "category": r["category"],
                    "raw_row_sha256": digest(r),
                    "status": "TYPED_MODEL_ROWS_WITH_EXPLICIT_HELD_DATES",
                    "evidence": ev,
                }
            )
    sink.finish()
    (root / "accounting.jsonl").write_bytes(b"".join(canonical(x) + b"\n" for x in lines))
    body.update(
        raw_row_accounting_sha256=file_sha(root / "accounting.jsonl"),
        output_rows=dict(sink.counts),
        raw_selected_counts=dict(rawcounts),
        raw_successful_categories=dict(successful),
        scalar_classes=dict(scalars),
    )
    seal(root, body)
    return body


def audit(root, body, tmp):
    limits = Limits(free_floor_bytes=1, allocated_bytes=16 * 1024**2, temp_bytes=16 * 1024**2)
    with Budget(tmp, [root], limits) as budget:
        return a.audit_ownership_observations(root, body, budget)


def test_full_raw_replay_reads_only_the_image_and_preserves_decimal_and_clocks(tmp_path):
    root = tmp_path / "image"
    body = image(root)
    result = audit(root, body, tmp_path / "tmp")
    assert result["raw_rows"] == 8
    assert result["output_rows"] == {t: 4 for t in TABLES}
    assert result["native_history_union"] == "NOT_QUALIFIED"


@pytest.mark.parametrize(
    "defect",
    ["decimal", "clock", "schema", "missing-shard", "disposition", "purpose", "extra-file"],
)
def test_fully_repacked_tampering_does_not_replace_raw_replay(tmp_path, defect):
    root = tmp_path / "image"
    body = image(root)
    item = body["outputs"][0]
    p = root / item["path"]
    if defect in {"decimal", "clock", "schema"}:
        table = pq.ParquetFile(p).read()
        rows = table.to_pylist()
        if defect == "decimal":
            rows[0]["share_quantity"] += 1
        elif defect == "clock":
            rows[0]["knowledge_from"] = datetime(2026, 8, 2, tzinfo=timezone.utc)
        else:
            table = table.drop(["raw_value"])
        if defect != "schema":
            table = pa.Table.from_pylist(rows, schema=table.schema)
        pq.write_table(table, p)
        item["semantic_scan"] = semantic_scan([p], table.schema)
    elif defect == "missing-shard":
        p.unlink()
        body["outputs"].pop(0)
    elif defect == "disposition":
        lines = [json.loads(x) for x in (root / "accounting.jsonl").read_text().splitlines()]
        lines[0]["evidence"]["actual_acquisition"] = "2026-08-02T00:00:00Z"
        (root / "accounting.jsonl").write_bytes(b"".join(canonical(x) + b"\n" for x in lines))
        body["raw_row_accounting_sha256"] = file_sha(root / "accounting.jsonl")
    elif defect == "purpose":
        body["purpose"] = "complete Base22"
    else:
        (root / "extra").write_text("unregistered")
    if defect != "extra-file":
        seal(root, body)
    with pytest.raises(BoundedError, match="OWNERSHIP"):
        audit(root, body, tmp_path / "tmp")


@pytest.mark.parametrize("shard,buffer", [(0, 4), (True, 4), (2049, 8192), (2, 1), (1, 8193)])
def test_resource_bounds_are_explicit(shard, buffer):
    rules = load_contract(Path("config/pipelines"))[0]["tables"]
    with pytest.raises(BoundedError, match="BUFFER_BOUND"):
        a.OwnershipReplaySink(rules, lambda *args: None, shard_rows=shard, buffer_rows=buffer)


def test_partition_shards_never_accumulate_the_whole_observation_history():
    rules = load_contract(Path("config/pipelines"))[0]["tables"]
    seen = []
    sink = a.OwnershipReplaySink(
        rules, lambda *args: seen.append(deepcopy(args)), shard_rows=2, buffer_rows=2
    )
    rows, _ = project(raw())
    for _ in range(5):
        sink.add(rows)
        assert sum(map(len, sink.buffers.values())) < 2
    sink.finish()
    assert [len(x[3]) for x in seen] == [2, 2, 1]
    assert [x[2] for x in seen] == [0, 1, 2]
    with pytest.raises(BoundedError, match="GROUP_SCOPE"):
        sink.add({"unknown": []})
