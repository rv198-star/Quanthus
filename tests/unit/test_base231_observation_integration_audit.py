import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import (
    canonical,
    digest,
    file_sha,
    load_contract,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, semantic_scan
from quanthus.pipelines.securities_warehouse.base22_sources import _tree
from quanthus.pipelines.securities_warehouse.base231_observation_integration import TABLES
from quanthus.pipelines.securities_warehouse.base231_observation_integration_audit import (
    CODE,
    QUALIFICATIONS,
    audit_observation_tmp,
)


def fixture(root):
    root.mkdir()
    (root / "config").mkdir()
    for n in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]:
        shutil.copyfile(Path("config/pipelines") / n, root / "config" / n)
    rules, model, ext = load_contract(root / "config")
    ss = schemas(model, ext)
    plan = dict(
        kind="P10_BASE231_EVENT_IDENTITY_INTEGRATION_TMP_V1",
        lifecycle="TMP",
        complete_Base=False,
        consumer_admission="NOT_GRANTED",
        business_through="2026-09-30",
        business_timezone="Asia/Shanghai",
        READY_written=False,
        historical_PIT_qualified=False,
        whole_image_acceptance=False,
        integrated_cross_table_references="PENDING_FULL_ASSEMBLY_GATES_SOURCE_DOMAINS_ALREADY_QUALIFIED",
        source_qualifications={k: dict(contract_id=v) for k, v in QUALIFICATIONS.items()},
        inputs={},
        layout={},
        proofs={},
        lineage={},
        summaries={},
        code_inputs={
            n: file_sha(Path("src/quanthus/pipelines/securities_warehouse") / n) for n in CODE
        },
    )
    for table in sorted(TABLES):
        values = []
        for prefix in ["old", "new"]:
            row = {}
            for f in ss[table]:
                if f.nullable:
                    row[f.name] = None
                elif pa.types.is_timestamp(f.type):
                    row[f.name] = datetime(2026, 9, 30, tzinfo=timezone.utc)
                elif pa.types.is_date(f.type):
                    row[f.name] = date(2026, 9, 30)
                elif pa.types.is_decimal(f.type):
                    row[f.name] = Decimal("1")
                elif pa.types.is_boolean(f.type):
                    row[f.name] = True
                elif pa.types.is_integer(f.type):
                    row[f.name] = 1
                else:
                    row[f.name] = prefix + f.name
            row["source_artifact_sha256"] = "sha256:" + "a" * 64
            values.append(row)
        plan["inputs"][table] = {}
        for key, rows in [("native", values[:1]), ("incoming", values[1:])]:
            p = root / "inputs" / table / (key + ".parquet")
            p.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), p)
            plan["inputs"][table][key] = [str(p.relative_to(root))]
        from quanthus.pipelines.securities_warehouse.base22 import partition

        part = partition(values[0], rules["tables"][table])
        p = root / "outputs" / table / part / "part-000000.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(values, schema=ss[table]), p)
        plan["layout"][table] = [
            dict(
                path=str(p.relative_to(root)),
                table=table,
                partition=part,
                rows=2,
                size_bytes=p.stat().st_size,
                sha256=file_sha(p),
            )
        ]
        plan["proofs"][table] = dict(
            native_rows=1,
            incoming_distinct_rows=1,
            incoming_exact_duplicates=0,
            exact_native_versions_reused=0,
            added_versions=1,
            integrated_rows=2,
            closed_native_intervals=0,
        )
        plan["lineage"][table] = [
            [r[k] for k in ["record_source", "source_revision", "source_artifact_sha256"]]
            for r in values
        ]
        plan["summaries"][table] = semantic_scan([p], ss[table])
    repack(root, plan)
    return plan


def repack(root, plan):
    plan["members"] = {
        n: dict(bytes=(root / n).stat().st_size, sha256=file_sha(root / n))
        for n in _tree(root)
        if n != "integration-TMP.json"
    }
    plan.pop("identity", None)
    plan["identity"] = digest(plan)
    (root / "integration-TMP.json").write_bytes(canonical(plan))


def test_all_tables_cold_reconstruct_from_local_dependencies_only(tmp_path):
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as b:
        root = b.root / "domain"
        p = fixture(root)
        result = audit_observation_tmp(root, b)
        assert result["identity"] == p["identity"]
        assert result["archived_roots_dereferenced"] is False
        assert result["integrated_cross_table_references"] == "NOT_RUN_FULL_ASSEMBLY_GATE"


@pytest.mark.parametrize("fault", ["value", "empty-dir", "unlisted", "qualification", "purpose"])
def test_fully_repacked_false_scope_or_changed_output_is_rejected(tmp_path, fault):
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as b:
        root = b.root / "domain"
        plan = fixture(root)
        if fault == "value":
            item = plan["layout"]["dwd_disclosure"][0]
            p = root / item["path"]
            data = pq.ParquetFile(p).read()
            rows = data.to_pylist()
            rows[0]["title"] = "changed sealed value"
            pq.write_table(pa.Table.from_pylist(rows, schema=data.schema), p)
            item.update(size_bytes=p.stat().st_size, sha256=file_sha(p))
            plan["summaries"]["dwd_disclosure"] = semantic_scan([p], data.schema)
        elif fault == "empty-dir":
            (root / "outputs/dwd_disclosure/extra").mkdir()
        elif fault == "unlisted":
            (root / "outputs/unknown.txt").write_text("synthetic")
        elif fault == "qualification":
            plan["source_qualifications"]["body"]["contract_id"] = "unqualified"
        else:
            plan["consumer_admission"] = "GRANTED"
        repack(root, plan)
        with pytest.raises(RuntimeError, match="OBSERVATION_TMP_"):
            audit_observation_tmp(root, b)
