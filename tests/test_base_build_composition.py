from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base23_native_image as native
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
)
from quanthus.pipelines.securities_warehouse.base_build_composition import (
    build_composition_plan,
    delivery_content,
    seal_standard_image,
)
from quanthus.pipelines.securities_warehouse.base_build_delta import build_delta
from quanthus.pipelines.securities_warehouse.base_build_inputs import (
    identity,
    inventory,
)
from quanthus.pipelines.securities_warehouse.base_build_profile import (
    REFERENCE_TABLES,
    UPDATED_TABLES,
    profile,
)
from tests.test_base_build_delta import period_fixture
from tests.test_base_build_profile import contract
from tests.unit.test_base22_native_image import _native_fixture


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    """Synthetic sealed predecessor headers, not an acceptance fixture."""
    old, config, source, old_plan = _native_fixture(tmp_path)
    old_plan["through_date"] = "2026-09-30"
    # Standard two-interval product; never infer minutes from day totals in runtime.
    bar_path = source / "dwd_security_bar.parquet"
    original = pq.ParquetFile(bar_path).read()
    rows = original.to_pylist()
    rows[0]["price_basis"] = "UNADJUSTED"
    minute = copy.deepcopy(rows[0])
    minute.update(
        interval_size="5m",
        observation_end=minute["observation_start"] + timedelta(minutes=5),
    )
    pq.write_table(pa.Table.from_pylist(rows + [minute], schema=original.schema), bar_path)
    item = old_plan["tables"]["dwd_security_bar"]["files"][0]
    item.update(sha256=file_sha(bar_path), size_bytes=bar_path.stat().st_size, rows=2)
    # Both intervals need their own declared physical partition.
    rule = old.contract["tables"]["dwd_security_bar"]
    from quanthus.pipelines.securities_warehouse.base22 import partition

    split = []
    for i, row in enumerate(rows + [minute]):
        p = source / f"bars-{i}.parquet"
        pq.write_table(pa.Table.from_pylist([row], schema=original.schema), p)
        split.append(
            {
                **item,
                "path": p.name,
                "sha256": file_sha(p),
                "rows": 1,
                "partition": partition(row, rule),
                "size_bytes": p.stat().st_size,
            }
        )
    old_plan["tables"]["dwd_security_bar"].update(
        files=split,
        rows=2,
        expected_partition_rows=dict(Counter(i["partition"] for i in split)),
    )
    predecessor = tmp_path / "predecessor"
    layout = {}
    for name, entry in old_plan["tables"].items():
        files = []
        for item in entry["files"]:
            rel = "payload/" + name + "/" + item["partition"] + "/" + Path(item["path"]).name
            target = predecessor / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / item["path"]).read_bytes())
            files.append({**item, "path": rel})
        layout[name] = {
            "files": files,
            "expected_partition_rows": entry["expected_partition_rows"],
        }
    save(predecessor / "evidence/input-plan.json", old_plan)
    record = {"layout": layout, "plan_digest": digest(old_plan)}
    save(predecessor / "MATERIALIZATION.json", record)
    save(
        predecessor / "manifest.json",
        {
            "kind": "dwd_base24_native_1d5m_purpose_qualified_candidate_v1",
            "business_through": "2026-09-30",
            "materialization_sha256": file_sha(predecessor / "MATERIALIZATION.json"),
        },
    )
    (predecessor / "READY").write_text(file_sha(predecessor / "manifest.json"))
    period_source = tmp_path / "period-inputs"
    period_source.mkdir()
    rules, schemas, tables, bar = period_fixture(period_source)
    c = contract()
    binding = {
        "predecessor_manifest_sha256": file_sha(predecessor / "manifest.json"),
        "frozen_input_identity": "sha256:" + "d" * 64,
        "code_revision": "e" * 40,
        "policy_identity": identity(c),
        "predecessor_through": "2026-09-30",
        "through_date": "2026-10-31",
        "target_version": "2.5",
    }
    with Budget(tmp_path / "period-job", [predecessor, period_source], limits()) as budget:
        delta = budget.root / "delta"
        build_delta(
            tables=tables,
            rules=rules,
            schemas=schemas,
            binding=binding,
            output=delta,
            budget=budget,
        )
    return predecessor, delta, rules, c, binding, old, config


def limits():
    return Limits(
        allocated_bytes=64 * 1024**2,
        temp_bytes=32 * 1024**2,
        free_floor_bytes=1,
        rss_bytes=4 * 1024**3,
        wall_seconds=120,
    )


def compose(tmp_path, f):
    predecessor, delta, rules, c, binding, _, _ = f
    with Budget(tmp_path / "composition-job", [predecessor, delta], limits()) as budget:
        return build_composition_plan(
            predecessor=predecessor,
            delta=delta,
            rules=rules,
            contract=c,
            binding=binding,
            budget=budget,
        )


def test_exact_33_tables_append_period_and_explicitly_preserve_other_coverage(tmp_path):
    f = fixture(tmp_path)
    plan = compose(tmp_path, f)
    assert len(plan["tables"]) == 33
    for table, entry in plan["tables"].items():
        disposition = plan["table_dispositions"][table]
        if table == "dwd_security_bar":
            assert entry["rows"] == 3
            assert set(entry["source_ids"]) == {"predecessor", "period"}
            assert len(entry["admitted_lineage"]) == 2
        else:
            assert entry["rows"] == 1
            assert entry["source_ids"] == ["predecessor"]
            assert all(item["partition"] != "empty" for item in entry["files"])
        assert disposition["action"] == (
            "APPEND_ADMITTED_PERIOD" if table in UPDATED_TABLES else "INHERIT"
        )
        assert disposition["coverage_statement"] == "ORIGINAL_COVERAGE_RETAINED_NO_REFRESH_INFERRED"
    # This plan is consumed by the shared native engine, not a one-off assembler.
    _, _, rules, c, _, old, config = f
    output = tmp_path / "complete-image"
    result = native.materialize_native_image(
        plan=plan,
        rules=rules,
        contract=c,
        model=old.model,
        extension=old.extension,
        limitations=[],
        local_evidence={},
        output=output,
        allocated_limit=64 * 1024**2,
        free_floor=1,
        rss_limit=4 * 1024**3,
        wall_seconds=120,
        config_root=config,
        profile=profile(c),
    )
    assert len(result["layout"]) == 33
    assert not (output / "READY").exists()
    content = delivery_content(output, c)
    save(
        output / "manifest.json",
        {"kind": "quanthus_dwd_base_bundle_v1", "content": content},
    )
    (output / "READY").write_text(file_sha(output / "manifest.json"))
    observed = inventory(output, file_sha(output / "manifest.json"))
    assert observed["file_count"] == len(content["files"]) + 2


@pytest.mark.parametrize(
    "damage",
    [
        "wrong-predecessor",
        "changed-plan",
        "changed-period",
        "extra-file",
        "missing-table",
        "wrong-binding",
    ],
)
def test_corrupt_or_incompatible_stage_cannot_be_composed(tmp_path, damage):
    f = fixture(tmp_path)
    predecessor, delta, _, _, binding, _, _ = f
    if damage == "wrong-predecessor":
        binding["predecessor_manifest_sha256"] = "sha256:" + "f" * 64
    elif damage == "changed-plan":
        (predecessor / "evidence/input-plan.json").write_text("{}")
    elif damage == "changed-period":
        path = next(delta.rglob("*.parquet"))
        path.write_bytes(path.read_bytes() + b"changed")
    elif damage == "extra-file":
        (delta / "unexpected.txt").write_text("not declared")
    elif damage == "missing-table":
        descriptor = json.loads((delta / "DELTA.json").read_text())
        descriptor["tables"].pop("dwd_index_bar")
        descriptor["identity"] = identity({k: v for k, v in descriptor.items() if k != "identity"})
        save(delta / "DELTA.json", descriptor)
    else:
        binding["code_revision"] = "f" * 40
    with pytest.raises(BoundedError, match="BUILD_COMPOSITION_"):
        compose(tmp_path, f)
    assert not (tmp_path / "complete-image").exists()


@pytest.mark.parametrize("damage", ["version", "cutoff", "code", "audit-receipt"])
def test_standard_envelope_cannot_relabel_another_scope_or_code(tmp_path, damage):
    c = contract()
    plan = {
        "through_date": c["business_through"],
        "build_binding": {
            "policy_identity": identity(c),
            "target_version": c["base_version"],
            "code_revision": "a" * 40,
        },
    }
    save(tmp_path / "contract/base-build-delivery.v1.json", c)
    save(tmp_path / "evidence/input-plan.json", plan)
    if damage == "version":
        c["base_version"] = "2.6"
    elif damage == "cutoff":
        c["business_through"] = "2026-11-30"
    code = "b" * 40 if damage == "code" else "a" * 40
    with pytest.raises(BoundedError, match="BUILD_COMPOSITION_"):
        seal_standard_image(tmp_path, {}, contract=c, code_revision=code)
    assert not (tmp_path / "READY").exists()


def test_new_effective_date_does_not_authorize_reusing_a_predecessor_identity(tmp_path):
    f = fixture(tmp_path)
    predecessor, delta, rules, _, binding, _, _ = f
    old_period = json.loads((delta / "DELTA.json").read_text())
    tables = {
        t: [delta / r["path"] for r in old_period["files"] if r["table"] == t]
        for t in UPDATED_TABLES
    }
    record = json.loads((predecessor / "MATERIALIZATION.json").read_text())
    for table in REFERENCE_TABLES:
        original = pq.ParquetFile(predecessor / record["layout"][table]["files"][0]["path"]).read()
        row = original.to_pylist()[0]
        row.update(
            effective_from=datetime(2026, 10, 14, tzinfo=timezone.utc),
            knowledge_from=datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
        p = tmp_path / (table + "-reused.parquet")
        pq.write_table(pa.Table.from_pylist([row], schema=original.schema), p)
        tables[table] = [p]
    from quanthus.pipelines.securities_warehouse.base23_native_image import (
        image_schemas,
    )

    schemas = image_schemas(
        json.loads(Path("config/pipelines/dwd-domain-model.v1.json").read_text()),
        json.loads(Path("config/pipelines/dwd-domain-model.v1.1-extension.json").read_text()),
    )
    with Budget(tmp_path / "changed-period", [predecessor, delta], limits()) as budget:
        new_delta = budget.root / "delta"
        build_delta(
            tables=tables,
            rules=rules,
            schemas=schemas,
            binding=binding,
            output=new_delta,
            budget=budget,
        )
    changed = (predecessor, new_delta, *f[2:])
    with pytest.raises(BoundedError, match="IDENTITY_EXTENSION_OVERLAPS_PREDECESSOR"):
        compose(tmp_path, changed)
