"""Synthetic complete financial scope, never real financial scalars."""

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_financial_layout as f
from quanthus.pipelines.securities_warehouse.base22 import file_sha, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits

C, M, E = load_contract(Path("config/pipelines"))
SS = {t: s for t, s in schemas(M, E).items() if t in f.TABLES}
RULES = {t: C["tables"][t] for t in f.TABLES}
MODEL = {"tables": [t for t in M["tables"] if t["name"] in f.TABLES]}
SHA = "sha256:" + "a" * 64


def row(table, *, year=2025):
    data = {}
    for field in SS[table]:
        if field.nullable:
            data[field.name] = None
        elif pa.types.is_string(field.type):
            data[field.name] = field.name
        elif pa.types.is_timestamp(field.type):
            data[field.name] = datetime(
                1980 if field.name == "effective_from" else 2026, 7, 1, tzinfo=timezone.utc
            )
        elif pa.types.is_date(field.type):
            data[field.name] = date(year, 12, 31)
        elif pa.types.is_boolean(field.type):
            data[field.name] = False
        else:
            data[field.name] = 1
    data.update(record_source="fixture", source_revision="v1", source_artifact_sha256=SHA)
    for key, value in [
        ("financial_report_id", "r"),
        ("financial_concept_id", "c"),
        ("company_id", "co"),
    ]:
        if key in data:
            data[key] = value
    if "value_decimal" in data:
        data["value_decimal"] = Decimal("1.0000000001")
    return data


def fixture(tmp_path, extra_year=False):
    root = tmp_path / "image"
    layout = {t: [] for t in f.TABLES}
    for t in sorted(f.TABLES):
        years = [2024, 2025] if extra_year and t in f.YEAR_TABLES else [2025]
        for year in years:
            part = "period_end_year=" + str(year) if t in f.YEAR_TABLES else "unpartitioned"
            put(root, layout, t, part, [row(t, year=year)])
    return root, layout


def put(root, layout, table, part, rows):
    parent = root / "payload" / RULES[table]["domain"] / table
    if table in f.YEAR_TABLES:
        parent /= part
    path = parent / (str(len(layout[table])) + ".parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SS[table]), path)
    layout[table].append(
        dict(
            path=path.relative_to(root).as_posix(),
            partition=part,
            rows=len(rows),
            size_bytes=path.stat().st_size,
            sha256=file_sha(path),
        )
    )


def change(root, layout, table, transform):
    item = layout[table][0]
    path = root / item["path"]
    rows = pq.ParquetFile(path).read().to_pylist()
    transform(rows[0])
    pq.write_table(pa.Table.from_pylist(rows, schema=SS[table]), path)
    item.update(size_bytes=path.stat().st_size, sha256=file_sha(path))


def audit(tmp_path, root, layout, monkeypatch, **changes):
    real = os.statvfs
    # Unit predicate fixture, separate from actual kernel-isolated acceptance.
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda p: SimpleNamespace(f_flag=os.ST_RDONLY) if Path(p) == root else real(p),
    )
    expected = {t: {} for t in f.TABLES}
    for t, items in layout.items():
        for i in items:
            expected[t][i["partition"]] = expected[t].get(i["partition"], 0) + i["rows"]
    args = (
        dict(
            schemas=SS,
            rules=RULES,
            model=MODEL,
            admitted={t: [("fixture", "v1", SHA)] for t in f.TABLES},
            expected_partition_rows=expected,
        )
        | changes
    )
    with Budget(
        tmp_path / "audit",
        [root],
        Limits(
            memory_bytes=128 * 1024**2,
            allocated_bytes=128 * 1024**2,
            temp_bytes=64 * 1024**2,
            free_floor_bytes=1,
        ),
    ) as budget:
        return f.audit_financial_layout(root, layout, budget=budget, **args)


def test_complete_parent_versions_and_full_keys_across_physical_years(tmp_path, monkeypatch):
    root, layout = fixture(tmp_path, extra_year=True)
    before = {p: file_sha(root / p) for p in f._tree(root)}
    result = audit(tmp_path, root, layout, monkeypatch)
    assert result["complete_global_parent_versions"]
    assert result["partition_rows"]["dwd_financial_fact"] == {
        "period_end_year=2024": 1,
        "period_end_year=2025": 1,
    }
    assert {p: file_sha(root / p) for p in f._tree(root)} == before
    assert result["source_qualification"] == "SEPARATE_REQUIRED_GATE"


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("duplicate", "GLOBAL_DUPLICATE_VERSION_KEY"),
        ("wrong-year", "PHYSICAL_YEAR_MISMATCH"),
        ("zero-partition", "EXACT_PARTITION_CENSUS_CHANGED"),
        ("unknown-source", "LINEAGE_CLOSURE_FAILED"),
        ("missing-company", "FOREIGN_KEY_DATE_VENUE_CLOSURE_FAILED"),
        ("future-parent", "FOREIGN_KEY_DATE_VENUE_CLOSURE_FAILED"),
        ("wrong-report-period", "REPORT_PERIOD_REFERENCE_MISSING"),
        ("extra-file", "PHYSICAL_FILE_SET_CHANGED"),
        ("bytes", "FROZEN_MEMBER_MISMATCH"),
        ("path", "PHYSICAL_TABLE_OR_PARTITION_MISMATCH"),
    ],
)
def test_resealed_physical_and_global_faults_fail_closed(tmp_path, monkeypatch, fault, reason):
    root, layout = fixture(tmp_path)
    fact, report = "dwd_financial_fact", "dwd_financial_report"
    if fault == "duplicate":
        put(root, layout, fact, "period_end_year=2025", [row(fact)])
    if fault == "wrong-year":
        change(root, layout, fact, lambda r: r.update(period_end=date(2024, 12, 31)))
    if fault == "zero-partition":
        put(root, layout, fact, "period_end_year=2024", [])
    if fault == "unknown-source":
        change(root, layout, fact, lambda r: r.update(record_source="unknown"))
    if fault == "missing-company":
        change(root, layout, report, lambda r: r.update(company_id="missing"))
    if fault == "future-parent":
        change(
            root,
            layout,
            report,
            lambda r: r.update(knowledge_from=datetime(2026, 8, 1, tzinfo=timezone.utc)),
        )
    if fault == "wrong-report-period":
        change(root, layout, report, lambda r: r.update(period_end=date(2025, 6, 30)))
    if fault == "extra-file":
        pq.write_table(pa.Table.from_pylist([], schema=SS[fact]), root / "payload/extra.parquet")
    if fault == "bytes":
        (root / layout[fact][0]["path"]).write_bytes(b"damage only synthetic copy")
    if fault == "path":
        item = layout[fact][0]
        old = root / item["path"]
        new = root / "payload/unknown/renamed.parquet"
        new.parent.mkdir()
        old.rename(new)
        item["path"] = new.relative_to(root).as_posix()
    with pytest.raises(BoundedError, match=reason):
        audit(tmp_path, root, layout, monkeypatch)


@pytest.mark.parametrize("fault", ["table", "model", "rule", "row-count"])
def test_count_or_unproven_partition_rule_cannot_replace_exact_contract(
    tmp_path, monkeypatch, fault
):
    root, layout = fixture(tmp_path)
    changes = {}
    if fault == "table":
        layout["unknown"] = layout.pop("dwd_listed_company")
    if fault == "model":
        changes["model"] = {"tables": MODEL["tables"][:-1]}
    if fault == "rule":
        rules = {t: dict(r) for t, r in RULES.items()}
        rules["dwd_financial_fact"]["business_key"] = ["financial_fact_id"]
        changes["rules"] = rules
    if fault == "row-count":
        layout["dwd_financial_fact"][0]["rows"] = 33
    with pytest.raises((BoundedError, KeyError)):
        audit(tmp_path, root, layout, monkeypatch, **changes)
