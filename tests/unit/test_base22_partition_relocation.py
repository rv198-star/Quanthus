from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
)
from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
    plan_relocation,
    relocate_member,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def fixture(tmp_path, *, wrong=True, rows=2):
    schema = pa.schema(
        [
            pa.field("effective_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC")),
            pa.field("value", pa.decimal128(18, 11)),
        ]
    )
    clocks = [
        datetime(2020, 12, 31, 17 if wrong else 7, tzinfo=timezone.utc),
        datetime(2020, 12, 1, 3, tzinfo=timezone.utc),
    ]
    data = pa.Table.from_pylist(
        [
            {
                "effective_at": clocks[i % 2],
                "knowledge_from": datetime(2026, 10, 6, tzinfo=timezone.utc),
                "knowledge_to": None,
                "value": [Decimal("0.12345678901"), None][i % 2],
            }
            for i in range(rows)
        ],
        schema=schema,
    )
    source = tmp_path / "source" / "input.parquet"
    source.parent.mkdir()
    pq.write_table(data, source)
    member = Member(source, file_sha(source), source.stat().st_size, rows)
    rule = {"partition_by": ["effective_at_year", "effective_at_month"]}
    return schema, member, rule, data


def test_shanghai_boundary_plan_and_exact_serialization(tmp_path):
    schema, member, rule, original = fixture(tmp_path)
    frozen_bytes = member.path.read_bytes()
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    assert plan["wrong_partition_rows"] == 1
    assert [p["partition"] for p in plan["routes"]] == [
        "effective_at_year=2020/effective_at_month=12",
        "effective_at_year=2021/effective_at_month=1",
    ]
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        result = relocate_member(
            member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=budget.root / "payload",
            budget=budget,
        )
        assert result["all_typed_values_exact"] and result["original_rows"] == 2
        assert sum(p["rows"] for p in result["files"]) == 2
        assert all((budget.root / p["path"]).stat().st_nlink == 1 for p in result["files"])
    assert member.path.read_bytes() == frozen_bytes
    assert original.column("value").to_pylist() == [Decimal("0.12345678901"), None]


@pytest.mark.parametrize(
    "table", ["dwd_disclosure", "dwd_disclosure_subject", "dwd_shareholding_change"]
)
def test_healthy_member_is_independent_exact_byte_copy(tmp_path, table):
    schema, member, rule, _ = fixture(tmp_path, wrong=False)
    plan = plan_relocation(
        member,
        table=table,
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        result = relocate_member(
            member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=budget.root / "payload",
            budget=budget,
        )
        dest = budget.root / result["files"][0]["path"]
        assert file_sha(dest) == member.sha256
        assert dest.stat().st_ino != member.path.stat().st_ino


def test_legacy_zero_padded_month_is_same_coordinate(tmp_path):
    schema, member, rule, _ = fixture(tmp_path, wrong=False)
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=02020/effective_at_month=012",
    )
    assert plan["original_partition"] == "effective_at_year=2020/effective_at_month=12"
    assert plan["wrong_partition_rows"] == 0


@pytest.mark.parametrize("mutation", ["route", "count", "identity"])
def test_plan_changes_fail_before_output(tmp_path, mutation):
    schema, member, rule, _ = fixture(tmp_path)
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    if mutation == "route":
        plan["routes"][0]["partition"] = "effective_at_year=2019/effective_at_month=12"
    elif mutation == "count":
        plan["routes"][0]["rows"] += 1
    else:
        plan["identity"] = "sha256:" + "0" * 64
    before = member.path.read_bytes()
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        with pytest.raises(BoundedError, match="FROZEN_PLAN"):
            relocate_member(
                member,
                proof=plan,
                schema=schema,
                rule=rule,
                output=budget.root / "payload",
                budget=budget,
            )
        assert not (budget.root / "payload").exists()
    assert member.path.read_bytes() == before


def test_member_ceiling_and_changed_bytes_reject(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    oversize = Member(member.path, member.sha256, member.size_bytes, 8193)
    with pytest.raises(BoundedError, match="ROW_CEILING"):
        plan_relocation(
            oversize, table="dwd_disclosure", schema=schema, rule=rule, original_partition="all"
        )
    member.path.write_bytes(member.path.read_bytes() + b"changed")
    with pytest.raises(BoundedError, match="FROZEN_MEMBER"):
        plan_relocation(
            member, table="dwd_disclosure", schema=schema, rule=rule, original_partition="all"
        )


def test_existing_output_is_not_overwritten(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        args = dict(
            member=member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=budget.root / "payload",
            budget=budget,
        )
        first = relocate_member(**args)
        before = {p["path"]: file_sha(budget.root / p["path"]) for p in first["files"]}
        with pytest.raises(BoundedError, match="OUTPUT_EXISTS"):
            relocate_member(**args)
        assert {p: file_sha(budget.root / p) for p in before} == before


def test_source_symlink_and_output_escape_leave_input_unchanged(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    before = member.path.read_bytes()
    linked = tmp_path / "linked.parquet"
    linked.symlink_to(member.path)
    with pytest.raises(SourceAssessmentError, match="SOURCE_SYMLINK"):
        plan_relocation(
            Member(linked, member.sha256, member.size_bytes, member.rows),
            table="dwd_disclosure",
            schema=schema,
            rule=rule,
            original_partition="all",
        )

    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    with (
        Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError),
    ):
        relocate_member(
            member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=member.path.parent / "escape",
            budget=budget,
        )
    assert not (member.path.parent / "escape").exists()
    assert member.path.read_bytes() == before


def test_september_business_observation_rejected_before_output(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    rows = pq.ParquetFile(member.path).read().to_pylist()
    rows[0]["effective_at"] = datetime(2026, 8, 31, 17, tzinfo=timezone.utc)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), member.path)
    updated = Member(member.path, file_sha(member.path), member.path.stat().st_size, 2)
    with pytest.raises(BoundedError, match="BUSINESS_CUTOFF_EXCEEDED"):
        plan_relocation(
            updated,
            table="dwd_disclosure",
            schema=schema,
            rule=rule,
            original_partition="effective_at_year=2026/effective_at_month=8",
        )


@pytest.mark.parametrize(
    "defect", [None, "missing", "corrupt", "empty_extra", "date", "source", "legacy", "producer"]
)
def test_complete_local_offline_relocation_scope(tmp_path, monkeypatch, defect):
    import json
    import os
    import shutil
    from pathlib import Path
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse.base22 import (
        canonical,
        digest,
        load_contract,
        schemas,
    )
    from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
        CONTRACT_ID,
        TABLES,
        audit_relocation_scope,
    )
    from tests.unit.test_base22_independent_image import _row

    source = tmp_path / "original"
    source.mkdir()
    root = tmp_path / "sealed"
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    plans, receipts, inherited = [], [], {}
    with Budget(root, [source], Limits(free_floor_bytes=1)) as budget:
        for t in sorted(TABLES):
            row = _row(ss[t], datetime(2020, 12, 31, 17, tzinfo=timezone.utc))
            if t == "dwd_disclosure_subject":
                row["security_id"] = "security"
            path = source / (t + ".parquet")
            pq.write_table(pa.Table.from_pylist([row], schema=ss[t]), path)
            member = Member(path, file_sha(path), path.stat().st_size, 1)
            plan = plan_relocation(
                member,
                table=t,
                schema=ss[t],
                rule=contract["tables"][t],
                original_partition="effective_at_year=2020/effective_at_month=12",
            )
            plans.append(plan)
            receipts.append(
                relocate_member(
                    member,
                    proof=plan,
                    schema=ss[t],
                    rule=contract["tables"][t],
                    output=root / "payload",
                    budget=budget,
                )
            )
            inherited[t] = [
                [row[k] for k in ["record_source", "source_revision", "source_artifact_sha256"]]
            ]
        parents = {}
        for t in [
            "dwd_security",
            "dwd_listed_company",
            "dwd_corporate_action",
            "dwd_financial_report",
        ]:
            path = root / "parents" / (t + ".parquet")
            path.parent.mkdir(exist_ok=True)
            pq.write_table(pa.Table.from_pylist([_row(ss[t])], schema=ss[t]), path)
            parents[t] = [{"path": str(path.relative_to(root)), "rows": 1}]
        (root / "contract").mkdir()
        for name in [
            "base22-independent-image.v1.json",
            "dwd-domain-model.v1.json",
            "dwd-domain-model.v1.1-extension.json",
        ]:
            shutil.copyfile(Path("config/pipelines") / name, root / "contract" / name)
        (root / "evidence").mkdir()
        (root / "evidence/plans.jsonl").write_bytes(b"".join(canonical(p) + b"\n" for p in plans))
        (root / "evidence/receipts.jsonl").write_bytes(
            b"".join(canonical(r) + b"\n" for r in receipts)
        )
    target = root / receipts[0]["files"][0]["path"]
    body = {
        "contract_id": CONTRACT_ID,
        "purpose": "EXACT_NATIVE_TYPED_RELOCATION_NOT_RAW_OR_PIT",
        "evaluator_sha256": file_sha(
            Path(__import__(audit_relocation_scope.__module__, fromlist=["x"]).__file__)
        ),
        "READY": False,
        "complete_Base22": False,
        "consumer_admission": "NOT_GRANTED",
        "relocated_table_rows": {t: 1 for t in TABLES},
        "parents": parents,
        "inherited_native_lineage": inherited,
    }
    if defect == "legacy":
        from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
            LEGACY_PRODUCER_SHA256,
        )

        identities = {}
        for plan in plans:
            previous = plan["identity"]
            plan["evaluator_sha256"] = LEGACY_PRODUCER_SHA256
            plan["identity"] = digest({k: v for k, v in plan.items() if k != "identity"})
            identities[previous] = plan["identity"]
        for receipt in receipts:
            receipt["plan_identity"] = identities[receipt["plan_identity"]]
            receipt["identity"] = digest({k: v for k, v in receipt.items() if k != "identity"})
        for name, values in [("plans", plans), ("receipts", receipts)]:
            (root / "evidence" / (name + ".jsonl")).write_bytes(
                b"".join(canonical(v) + b"\n" for v in values)
            )
        body["evaluator_sha256"] = LEGACY_PRODUCER_SHA256
    if defect == "producer":
        body["evaluator_sha256"] = "sha256:" + "0" * 64
    if defect in ["date", "source"]:
        t = plans[0]["table"]
        rows = pq.ParquetFile(target).read().to_pylist()
        if defect == "date":
            rows[0]["effective_at"] = datetime(2020, 12, 15, tzinfo=timezone.utc)
        else:
            rows[0]["source_revision"] = "unknown-new-source"
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[t]), target)
        receipts[0]["files"][0]["sha256"] = file_sha(target)
        receipts[0]["identity"] = digest({k: v for k, v in receipts[0].items() if k != "identity"})
        (root / "evidence/receipts.jsonl").write_bytes(
            b"".join(canonical(r) + b"\n" for r in receipts)
        )
    if defect == "empty_extra":
        pq.write_table(
            pa.Table.from_pylist([], schema=ss[plans[0]["table"]]), root / "payload/extra.parquet"
        )
    body["physical_files"] = {
        str(p.relative_to(root)): file_sha(p) for p in root.rglob("*") if p.is_file()
    }
    body["identity"] = digest(body)
    (root / "QUALIFICATION.json").write_bytes(canonical(body))
    if defect == "missing":
        target.unlink()
    if defect == "corrupt":
        target.write_bytes(b"corrupt")
    real = os.statvfs
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda p: SimpleNamespace(f_flag=os.ST_RDONLY) if Path(p) == root else real(p),
    )
    decoded = json.loads(canonical(body))
    with Budget(tmp_path / "audit", [root], Limits(free_floor_bytes=1)) as budget:
        if defect in [None, "legacy"]:
            assert audit_relocation_scope(root, decoded, budget)["rows"] == {t: 1 for t in TABLES}
        else:
            with pytest.raises(BoundedError):
                audit_relocation_scope(root, decoded, budget)


@pytest.mark.parametrize("defect", [None, "duplicate", "overlap", "null", "not_key_derived"])
def test_key_derived_month_checks_all_files_and_exact_versions(tmp_path, defect):
    from datetime import timedelta

    from quanthus.pipelines.securities_warehouse.base22_bounded import GlobalScan
    from quanthus.pipelines.securities_warehouse.base22_partition_relocation import _month_keys

    clock = datetime(2026, 8, 1, tzinfo=timezone.utc)
    first = {"id": "synthetic", "effective_at": clock, "knowledge_from": clock,
             "knowledge_to": clock + timedelta(days=1)}
    second = dict(first, knowledge_from=clock + timedelta(days=1), knowledge_to=None)
    if defect == "duplicate":
        second = dict(first)
    elif defect == "overlap":
        first["knowledge_to"] = None
    elif defect == "null":
        second["knowledge_from"] = None
    rule = {"business_key": ["id", "effective_at"],
            "version_key": ["id", "effective_at", "knowledge_from"],
            "partition_by": ["effective_at_year", "effective_at_month"]}
    if defect == "not_key_derived":
        rule["business_key"] = ["id"]
        rule["version_key"] = ["id", "knowledge_from"]
    schema = pa.schema([("id", pa.string()), *[
        (f, pa.timestamp("us", tz="UTC"))
        for f in ["effective_at", "knowledge_from", "knowledge_to"]
    ]])
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget:
        paths = []
        for n, row in enumerate([first, second]):
            p = budget.root / (str(n) + ".parquet")
            pq.write_table(pa.Table.from_pylist([row], schema=schema), p)
            paths.append(p)
        with GlobalScan(budget) as scan:
            scan.add("month", paths)
            if defect:
                with pytest.raises(BoundedError, match="GLOBAL_|MONTH_KEY_CLOSURE"):
                    _month_keys(scan, "month", rule)
            else:
                _month_keys(scan, "month", rule)
                assert not list(scan.temp.rglob("*.parquet"))


@pytest.mark.parametrize("defect", [None, "first", "second", "nullable", "same_parent"])
def test_fused_structural_references_scan_children_once_against_global_parents(tmp_path, defect):
    from quanthus.pipelines.securities_warehouse.base22_bounded import GlobalScan
    from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
        _structural_references,
    )
    from tests.unit.test_base22_bounded import records

    n = 66
    keys = list(reversed(range(n)))
    other = keys if defect == "same_parent" else [k + 100 for k in keys]
    if defect == "first":
        keys[-1] = 999
    elif defect == "second":
        other[-1] = 999
    elif defect == "nullable":
        other[-1] = None
    refs = [
        {"column": "key", "table": "first_parent", "reference_column": "key"},
        {
            "column": "other",
            "table": "first_parent" if defect == "same_parent" else "second_parent",
            "reference_column": "key",
        },
    ]
    with (
        Budget(tmp_path / "job", [], Limits(batch_rows=1, free_floor_bytes=1)) as budget,
        GlobalScan(budget) as scan,
    ):
        for table, start in [("first_parent", 0), ("second_parent", 100)]:
            parent_paths = []
            for i in range(2):
                p = budget.root / f"{table}-{i}.parquet"
                pq.write_table(records(tuple(range(start + i * 33, start + (i + 1) * 33))), p)
                parent_paths.append(p)
            scan.add(table, parent_paths)
        child = records(tuple(keys)).append_column("other", pa.array(other, pa.int64()))
        paths = []
        for i in range(2):
            p = budget.root / f"child-{i}.parquet"
            pq.write_table(child.slice(i * 33, 33), p)
            paths.append(p)
        scan.add("child", paths)
        plans, queries, parent_keys = [], [], {}
        reject = scan.reject

        def inspect(sql, reason):
            queries.append(sql)
            plans.append(scan.connection.execute("EXPLAIN " + sql).fetchone()[1])
            return reject(sql, reason)

        scan.reject = inspect
        if defect in ["first", "second"]:
            with pytest.raises(BoundedError, match="RELOCATION_MODEL_FOREIGN_KEY_MISSING"):
                _structural_references(scan, "child", refs, parent_keys=parent_keys)
        else:
            _structural_references(scan, "child", refs, parent_keys=parent_keys)
        assert len(queries) == 2
        assert all("HASH_JOIN" in plan and "DELIM_JOIN" not in plan
                   and "BLOCKWISE_NL_JOIN" not in plan for plan in plans)
        assert len(parent_keys) == (1 if defect == "same_parent" else 2)
        assert not scan._reference_maps
        assert scan.connection.execute("SELECT count(*) FROM child").fetchone()[0] == n


@pytest.mark.parametrize("bad", [None, "missing", "null", "two_subjects"])
def test_compact_global_parent_keys_reused_and_subject_rule_preserved(tmp_path, bad):
    from quanthus.pipelines.securities_warehouse.base22_bounded import GlobalScan
    from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
        _structural_references,
    )
    from tests.unit.test_base22_bounded import records

    refs = [{"column": "security_id", "table": "parent", "reference_column": "key"}]
    keys = {}
    with (Budget(tmp_path / "job", [], Limits(batch_rows=1, free_floor_bytes=1)) as budget,
          GlobalScan(budget) as scan):
        parent = records((0, 1, 1, 2))
        p = budget.root / "parent.parquet"
        pq.write_table(parent, p)
        scan.add("parent", [p])
        for name in ["first", "second"]:
            child = pa.table({"security_id": [0, 999 if bad == "missing" else 2],
                              "corporate_action_id": pa.array(
                                  [None, 1 if bad == "two_subjects" else None], pa.int64()),
                              "financial_report_id": pa.array([None, None], pa.int64())})
            if bad == "null":
                child = child.set_column(0, "security_id", pa.array([0, None], pa.int64()))
            path = budget.root / (name + ".parquet")
            pq.write_table(child, path)
            scan.add(name, [path])
            if bad:
                with pytest.raises(BoundedError, match="FOREIGN_KEY_MISSING|SUBJECT_IDENTIFIER"):
                    _structural_references(scan, name, refs, parent_keys=keys, subject_rule=True)
            else:
                assert _structural_references(
                    scan, name, refs, parent_keys=keys, subject_rule=True
                ) == 1
        assert len(keys) == 1
        keymap = next(iter(keys.values()))
        assert scan.connection.execute("SELECT count(*) FROM " + keymap).fetchone()[0] == 3
        fields = [r[0] for r in scan.connection.execute("DESCRIBE " + keymap).fetchall()]
        assert fields == ["parent_key"]
        assert not scan._reference_maps  # no unused temporal-list materialization


@pytest.mark.parametrize("bad", [None, "source", "row_id", "cutoff", "interval", "empty_registry"])
def test_fused_lineage_cutoff_retains_shanghai_boundary_and_unknowns(tmp_path, bad):
    from quanthus.pipelines.securities_warehouse.base22_bounded import GlobalScan
    from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
        _lineage_and_cutoff,
    )

    clock = datetime(2026, 8, 31, 15, 59, 59, 999999, tzinfo=timezone.utc)
    row = {"effective_at": clock, "knowledge_from": clock, "knowledge_to": None,
           "source_record_id": "synth", "record_source": "synthetic",
           "source_revision": "one", "source_artifact_sha256": "sha256:" + "a" * 64}
    admitted = [(row["record_source"], row["source_revision"], row["source_artifact_sha256"])]
    if bad == "source":
        row["source_revision"] = "unknown"
    elif bad == "row_id":
        row["source_record_id"] = ""
    elif bad == "cutoff":
        row["effective_at"] = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
    elif bad == "interval":
        row["knowledge_to"] = row["knowledge_from"]
    elif bad == "empty_registry":
        admitted = []
    schema = pa.schema([(f, pa.timestamp("us", tz="UTC")) for f in
                        ["effective_at", "knowledge_from", "knowledge_to"]] +
                       [(f, pa.string()) for f in ["source_record_id", "record_source",
                                                   "source_revision", "source_artifact_sha256"]])
    with (Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget,
          GlobalScan(budget) as scan):
        path = budget.root / "one.parquet"
        pq.write_table(pa.Table.from_pylist([row], schema=schema), path)
        scan.add("synthetic", [path])
        if bad:
            with pytest.raises(BoundedError, match="LINEAGE_OR_BUSINESS|SOURCE_CLOSURE_EMPTY"):
                _lineage_and_cutoff(scan, "synthetic", admitted)
        else:
            _lineage_and_cutoff(scan, "synthetic", admitted)
