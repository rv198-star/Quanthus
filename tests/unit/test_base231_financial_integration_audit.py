import json
import shutil
from datetime import timedelta
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
)
from quanthus.pipelines.securities_warehouse.base22 import schemas as image_schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan, Limits
from quanthus.pipelines.securities_warehouse.base22_sources import _tree
from quanthus.pipelines.securities_warehouse.base231_financial_integration import (
    integrate_concepts,
    report_revision_views,
)
from quanthus.pipelines.securities_warehouse.base231_financial_integration_audit import (
    QUALIFICATIONS,
    audit_financial_tmp,
)
from tests.unit.test_base231_financial_integration import rows


def fixture(budget):
    root = budget.root / "domain"
    root.mkdir()
    (root / "config").mkdir()
    for name in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]:
        shutil.copyfile(Path("config/pipelines") / name, root / "config" / name)
    _, model, ext = load_contract(root / "config")
    ss = image_schemas(model, ext)
    values = rows()
    new_report = values["dwd_financial_report"][0]
    old_report = dict(new_report, knowledge_from=new_report["knowledge_from"] - timedelta(days=1))
    new_concept = values["dwd_financial_concept"][0]
    old_concept = dict(new_concept, knowledge_from=old_report["knowledge_from"])
    concepts, proof = integrate_concepts([old_concept], [new_concept])
    company = dict(
        old_concept,
        company_id=new_report["company_id"],
        display_name="synthetic",
        company_type="synthetic",
        resolution_scope="synthetic",
    )
    inputs = {}

    def write(path, table, records):
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(records, schema=ss[table]), p)
        return p

    for t, records in values.items():
        path = "inputs/" + t + ".parquet"
        write(path, t, records)
        inputs[t] = [path]
    write("inputs/native-concepts.parquet", "dwd_financial_concept", [old_concept])
    write("inputs/native-reports.parquet", "dwd_financial_report", [old_report])
    write("inputs/companies.parquet", "dwd_listed_company", [company])
    with GlobalScan(budget) as scan:
        scan.add("old_reports", [root / "inputs/native-reports.parquet"])
        scan.add("incoming_reports", [root / inputs["dwd_financial_report"][0]])
        reports = report_revision_views(scan)
    emitted = dict(
        values,
        dwd_financial_concept=concepts,
        dwd_financial_report=[
            dict(old_report, knowledge_to=new_report["knowledge_from"]),
            new_report,
        ],
    )
    layout, lineage = {}, {}
    for table, records in emitted.items():
        part = "all" if table == "dwd_financial_concept" else "period_end_year=2026"
        path = "outputs/" + table + "/" + part + "/part-000000.parquet"
        p = write(path, table, records)
        layout[table] = [
            dict(
                table=table,
                partition=part,
                path=path,
                rows=len(records),
                sha256=file_sha(p),
                size_bytes=p.stat().st_size,
            )
        ]
        lineage[table] = sorted(
            {
                tuple(r[k] for k in ["record_source", "source_revision", "source_artifact_sha256"])
                for r in records
            }
        )
    names = [
        "base231_financial_integration.py",
        "base22_outputs.py",
        "base231_image.py",
        "base22_bounded.py",
        "base23_native_keys.py",
        "base22_native_keys.py",
    ]
    plan = dict(
        kind="P10_BASE231_FINANCIAL_INTEGRATION_TMP_V1",
        lifecycle="TMP",
        complete_Base=False,
        consumer_admission="NOT_GRANTED",
        historical_PIT_qualified=False,
        business_through="2026-09-30",
        business_timezone="Asia/Shanghai",
        READY_written=False,
        source_qualifications=[dict(contract_id=q) for q in sorted(QUALIFICATIONS)],
        inputs=inputs,
        layout=layout,
        lineage=lineage,
        concept_proof=proof,
        report_proof=reports,
        native_concepts="inputs/native-concepts.parquet",
        native_reports="inputs/native-reports.parquet",
        companies="inputs/companies.parquet",
        code_inputs={
            n: file_sha(Path("src/quanthus/pipelines/securities_warehouse") / n) for n in names
        },
    )
    package(root, plan)
    return root, plan


def package(root, plan):
    plan["members"] = {
        n: dict(bytes=(root / n).stat().st_size, sha256=file_sha(root / n))
        for n in _tree(root)
        if n != "integration-TMP.json"
    }
    for files in plan["layout"].values():
        for m in files:
            m.update(
                sha256=file_sha(root / m["path"]), size_bytes=(root / m["path"]).stat().st_size
            )
    plan["identity"] = digest({k: v for k, v in plan.items() if k != "identity"})
    (root / "integration-TMP.json").write_bytes(canonical(plan))


def test_full_offline_financial_reconstruction_preserves_typed_atoms(tmp_path):
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget:
        root, _ = fixture(budget)
        result = audit_financial_tmp(root, budget)
        assert result["original_report_fact_columns"] == "PASS"
        assert result["archived_roots_dereferenced"] is False
        assert result["complete_Base"] is False


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("value", "ORIGINAL_TYPED_ATOM"),
        ("source", "ORIGINAL_TYPED_ATOM"),
        ("concept", "OLD_OR_NEW_CONCEPT_ATOM"),
        ("purpose", "PURPOSE"),
        ("qualification", "QUALIFICATION"),
        ("extra-empty", "SERIALIZED_SCHEMA_CENSUS"),
        ("unlisted-empty", "OUTPUT_PHYSICAL_SET"),
        ("empty-directory", "OUTPUT_DIRECTORY_SET"),
    ],
)
def test_fully_rehashed_copy_cannot_bypass_financial_semantics(tmp_path, fault, reason):
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget:
        root, plan = fixture(budget)
        if fault in {"value", "source"}:
            table = "dwd_financial_fact"
            p = root / plan["layout"][table][0]["path"]
            native = pq.ParquetFile(p).read()
            record = native.to_pylist()[0]
            record["value_decimal" if fault == "value" else "source_record_id"] = (
                Decimal("1.0000000000") if fault == "value" else "unknown-original-record"
            )
            pq.write_table(pa.Table.from_pylist([record], schema=native.schema), p)
        elif fault == "concept":
            p = root / plan["layout"]["dwd_financial_concept"][0]["path"]
            native = pq.ParquetFile(p).read()
            r = native.to_pylist()[0]
            r["concept_name"] = "rehashed-synthetic-conflict"
            pq.write_table(pa.Table.from_pylist([r], schema=native.schema), p)
        elif fault == "purpose":
            plan["complete_Base"] = True
        elif fault == "qualification":
            plan["source_qualifications"] = []
        elif fault == "empty-directory":
            (root / "outputs/dwd_financial_fact/unlisted-partition").mkdir()
        else:
            table = "dwd_financial_fact"
            original = plan["layout"][table][0]
            p = root / original["path"]
            target = root / "outputs" / table / "period_end_year=2025/part-000000.parquet"
            target.parent.mkdir()
            pq.write_table(pq.ParquetFile(p).read().slice(0, 0), target)
            if fault != "unlisted-empty":
                plan["layout"][table].append(
                    dict(
                        original,
                        partition="period_end_year=2025",
                        path=str(target.relative_to(root)),
                        rows=0,
                    )
                )
        package(root, plan)
        assert (
            json.loads((root / "integration-TMP.json").read_bytes())["identity"] == plan["identity"]
        )
        with pytest.raises(RuntimeError, match=reason):
            audit_financial_tmp(root, budget)
