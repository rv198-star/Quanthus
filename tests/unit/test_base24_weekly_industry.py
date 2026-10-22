"""Successor dates/receipts must not widen the legacy historical industry contract."""

from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base24_weekly_industry import (
    project_snapshot,
    snapshot_observation,
)
from tests.unit.test_base23_industry import fixture as legacy_fixture


def fixture():
    raw, old = legacy_fixture()
    raw.update(
        market_date="2026-10-17",
        acquired_at="2026-10-17T09:11:34+08:00",
        provider_update_date="2026-10-12",
    )
    receipt = dict(
        dataset="industry_membership",
        security_id="600001",
        provider="baostock",
        status="success_nonempty",
        response_rows=1,
        response_sha256=None,
        acquired_at=raw["acquired_at"],
        request_json='{"operation":"query_stock_industry","scope":"all"}',
    )
    kw = dict(
        day=date(2026, 10, 17),
        parent=old["parent"],
        node=old["node"],
        observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        source_sha256=old["source_sha256"],
        provider_revision=raw["source_revision"],
        dataset_revision="original-dataset-v2",
    )
    return raw, receipt, kw


def test_typed_snapshot_keeps_capture_update_observation_and_missing_hash_separate():
    raw, receipt, kw = fixture()
    before = deepcopy((raw, receipt, kw))
    row, proof = snapshot_observation(raw, receipt, **kw)
    assert (raw, receipt, kw) == before
    assert row["source_declared_as_of"] == date(2026, 10, 17)
    assert row["effective_from"] == datetime(2026, 10, 16, 16, tzinfo=timezone.utc)
    assert row["knowledge_from"] == kw["observed_at"]
    assert proof["original_acquired_at"] == raw["acquired_at"]
    assert proof["original_provider_update_date"] == "2026-10-12"
    assert proof["original_response_sha256"] is None
    assert proof["original_provider_revision"] != row["source_revision"]
    assert not proof["historical_PIT_qualified"]
    assert not proof["native_history_rewritten"]
    assert not proof["full_current_market_cohort_qualified"]


@pytest.mark.parametrize(
    "defect",
    [
        "extra-field",
        "different-code",
        "different-day",
        "september",
        "future-capture",
        "provider-revision",
        "classification",
        "source",
        "receipt-code",
        "receipt-status",
        "receipt-time",
        "receipt-request",
        "receipt-rows",
        "missing-parent",
        "node-name",
        "node-effective",
        "node-knowledge",
        "update-date",
        "submicrosecond",
    ],
)
def test_source_receipt_and_time_defects_reject_without_mutating_inputs(defect):
    raw, receipt, kw = fixture()
    if defect == "extra-field":
        raw["extra"] = "not ignored"
    elif defect == "different-code":
        raw["security_id"] = "600002"
    elif defect == "different-day":
        raw["market_date"] = "2026-10-16"
    elif defect == "september":
        kw["day"] = date(2026, 9, 30)
        raw["market_date"] = "2026-09-30"
    elif defect == "future-capture":
        raw["acquired_at"] = "2026-10-21T00:00:00+08:00"
    elif defect == "provider-revision":
        raw["source_revision"] = "wrong"
    elif defect == "classification":
        raw["classification"] = "unknown"
    elif defect == "source":
        raw["source"] = "unknown"
    elif defect == "receipt-code":
        receipt["security_id"] = "600002"
    elif defect == "receipt-status":
        receipt["status"] = "failed"
    elif defect == "receipt-time":
        receipt["acquired_at"] = "2026-10-17T09:12:00+08:00"
    elif defect == "receipt-request":
        receipt["request_json"] = "{}"
    elif defect == "receipt-rows":
        receipt["response_rows"] = True
    elif defect == "missing-parent":
        kw["parent"] = None
    elif defect == "node-name":
        kw["node"]["node_name"] = "different"
    elif defect == "node-effective":
        kw["node"]["effective_from"] = kw["observed_at"]
    elif defect == "node-knowledge":
        kw["node"]["knowledge_to"] = kw["observed_at"]
    elif defect == "update-date":
        raw["provider_update_date"] = "unknown"
    else:
        raw["acquired_at"] = "2026-10-17T09:11:34.1234567+08:00"
    before = deepcopy((raw, receipt, kw))
    with pytest.raises((BoundedError, RuntimeError)):
        snapshot_observation(raw, receipt, **kw)
    assert (raw, receipt, kw) == before


def test_whole_cohort_exact_receipts_nodes_and_output_keys():
    raw, receipt, kw = fixture()
    parent = kw.pop("parent")
    node = kw.pop("node")
    resolver = SimpleNamespace(resolve_for_report=lambda code, period_end: (parent, None))
    rows, proof = project_snapshot([raw], [receipt], resolver=resolver, nodes=[node], **kw)
    assert len(rows) == proof["source_row_census"] == proof["receipt_census"] == 1
    assert proof["missing_original_response_hashes"] == 1
    for raws, receipts, nodes in [
        ([raw, raw], [receipt, receipt], [node]),
        ([raw], [], [node]),
        ([raw], [receipt], [node, node]),
    ]:
        with pytest.raises(BoundedError):
            project_snapshot(raws, receipts, resolver=resolver, nodes=nodes, **kw)


def scope_fixture(tmp_path):
    import json
    import shutil
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import (
        canonical,
        file_sha,
        load_contract,
        schemas,
    )
    from quanthus.pipelines.securities_warehouse.base24_weekly_industry import (
        CONTRACT,
        CONTRACT_NAMES,
        KIND,
        QUALIFICATION,
        REFERENCE_TABLES,
        TABLE,
        industry_parent,
    )
    from tests.unit.test_base24_weekly_financial import weekly_config
    from tests.unit.test_native_base24_reader import package, reseal

    raw, receipt, kw = fixture()
    base, manifest, mat, cfg = package(tmp_path)
    early = datetime(1990, 1, 1, tzinfo=timezone.utc)
    common = dict(effective_from=early, effective_to=None, knowledge_from=early, knowledge_to=None)
    reference_rows = {
        "dwd_security": [{**common, "security_id": "s", "company_id": "company-one"}],
        "dwd_security_identifier": [
            {
                **common,
                "security_id": "s",
                "identifier_namespace": "CN_STOCK_CODE:SSE",
                "identifier_value": "600001",
            }
        ],
        "dwd_listed_company": [{"company_id": "company-one"}],
        "dwd_industry_node": [{**kw["node"], "industry_scheme_id": "scheme"}],
        "dwd_industry_scheme": [{"industry_scheme_id": "scheme"}],
        "dwd_trading_venue": [{"trading_venue_id": "venue"}],
    }
    for table, values in reference_rows.items():
        rel = f"payload/{table}/all/part-000000.parquet"
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(values), p)
        mat["layout"][table] = dict(
            files=[
                dict(
                    path=rel,
                    table=table,
                    partition="all",
                    rows=1,
                    size_bytes=p.stat().st_size,
                    sha256=file_sha(p),
                )
            ],
            expected_partition_rows={"all": 1},
        )
        manifest["summaries"][table] = {"all": {"rows": 1}}
    reseal(base, manifest, mat, cfg)
    config = weekly_config()
    config["layers"] = cfg["layers"]
    config["weekly_industry_reference"] = dict(
        status="accepted_reference_only",
        tables=list(REFERENCE_TABLES),
        trading_or_research_admission=False,
        acceptance_receipts=config["weekly_identity_reference"]["acceptance_receipts"],
    )
    config["industry_snapshot_source"] = dict(
        provider_revision=raw["source_revision"], qualification=QUALIFICATION
    )
    root = tmp_path / "industry"
    for name in ["sources", "references", "contract", "tables"]:
        (root / name).mkdir(parents=True)
    for name in ["manifest.json", "MATERIALIZATION.json", "READY"]:
        shutil.copyfile(base / name, root / "references" / name)
    for table in REFERENCE_TABLES:
        for e in mat["layout"][table]["files"]:
            p = root / "references" / e["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / e["path"], p)
    for name in CONTRACT_NAMES:
        shutil.copyfile(Path("config/pipelines") / name, root / "contract" / name)
    raw_file = root / "sources/industry_membership.parquet"
    receipt_file = root / "sources/industry_membership.parquet.receipts.parquet"
    pq.write_table(pa.Table.from_pylist([raw]), raw_file)
    pq.write_table(pa.Table.from_pylist([receipt]), receipt_file)
    control = dict(
        datasets=[
            dict(
                name="industry_membership",
                status="ready",
                sha256=file_sha(raw_file),
                source_revision=kw["dataset_revision"],
                row_count=1,
                watermark=kw["day"].isoformat(),
                source_request=json.dumps(
                    dict(
                        receipt_count=1,
                        receipt_sha256=file_sha(receipt_file),
                        provider_metadata=dict(provider_revision=kw["provider_revision"]),
                    )
                ),
            )
        ]
    )
    (root / "sources/datasets.json").write_bytes(canonical(control) + b"\n")
    (root / "weekly-config.json").write_bytes(canonical(config) + b"\n")
    parent, resolver, refs = industry_parent(root / "references", config)
    kw.pop("parent")
    kw.pop("node")
    kw["source_sha256"] = file_sha(raw_file)
    rows, proof = project_snapshot(
        [raw], [receipt], resolver=resolver, nodes=refs["dwd_industry_node"], **kw
    )
    _, model, ext = load_contract(root / "contract")
    pq.write_table(
        pa.Table.from_pylist(rows, schema=schemas(model, ext)[TABLE]),
        root / "tables" / (TABLE + ".parquet"),
    )
    body = dict(
        kind=KIND,
        contract_id=CONTRACT,
        qualification=QUALIFICATION,
        consumer_admission="NOT_GRANTED",
        execution_authorized=False,
        complete_weekly_admission=False,
        historical_PIT_admission=False,
        business_date=kw["day"].isoformat(),
        observed_at=kw["observed_at"].isoformat(),
        provider_revision=kw["provider_revision"],
        dataset_revision=kw["dataset_revision"],
        projection_module_sha256=file_sha(
            Path(
                __import__(
                    "quanthus.pipelines.securities_warehouse.base24_weekly_industry",
                    fromlist=["__file__"],
                ).__file__
            )
        ),
        base_binding=dict(parent.binding),
        projection=proof,
        files={
            p.relative_to(root).as_posix(): dict(sha256=file_sha(p), bytes=p.stat().st_size)
            for p in root.rglob("*")
            if p.is_file()
        },
    )
    (root / "manifest.json").write_bytes(canonical(body) + b"\n")
    sha = file_sha(root / "manifest.json")
    (root / "READY").write_text(sha + "\n")
    return root, sha, base


def test_industry_scope_two_independent_moves_without_original_reference(tmp_path):
    import shutil

    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base24_weekly_industry import verify_snapshot_scope

    root, sha, base = scope_fixture(tmp_path)
    first, second = tmp_path / "move1", tmp_path / "move2"
    shutil.copytree(root, first)
    shutil.copytree(first, second)
    shutil.rmtree(root)
    shutil.rmtree(base)
    outcomes = []
    for i, moved in enumerate([first, second]):
        with Budget(tmp_path / f"scratch-{i}", [moved], Limits(free_floor_bytes=1)) as budget:
            outcomes.append(verify_snapshot_scope(moved, manifest_sha256=sha, budget=budget))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0]["status"] == "PASS_DATED_INDUSTRY_COHORT_ONLY"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "corrupt",
        "row-repacked",
        "role-repacked",
        "receipt-repacked",
        "extra-empty-repacked",
    ],
)
def test_industry_scope_rejects_fully_repacked_semantic_tampering(tmp_path, mutation):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base24_weekly_industry import (
        TABLE,
        verify_snapshot_scope,
    )

    root, sha, _ = scope_fixture(tmp_path)
    body = json.loads((root / "manifest.json").read_text())
    target = root / "tables" / (TABLE + ".parquet")
    if mutation == "missing":
        target.unlink()
    elif mutation == "corrupt":
        target.write_bytes(b"damaged disposable bytes")
    else:
        if mutation == "row-repacked":
            typed = pq.read_table(target)
            rows = typed.to_pylist()
            rows[0]["source_declared_as_of"] = date(2026, 10, 16)
            pq.write_table(pa.Table.from_pylist(rows, schema=typed.schema), target)
        elif mutation == "role-repacked":
            target = root / "weekly-config.json"
            cfg = json.loads(target.read_text())
            cfg["purpose"] = "production_execution"
            target.write_bytes(canonical(cfg) + b"\n")
        elif mutation == "receipt-repacked":
            target = root / "sources/industry_membership.parquet.receipts.parquet"
            typed = pq.read_table(target)
            rows = typed.to_pylist()
            rows[0]["status"] = "failed"
            pq.write_table(pa.Table.from_pylist(rows, schema=typed.schema), target)
            control = root / "sources/datasets.json"
            cfg = json.loads(control.read_text())
            req = json.loads(cfg["datasets"][0]["source_request"])
            req["receipt_sha256"] = file_sha(target)
            cfg["datasets"][0]["source_request"] = json.dumps(req)
            control.write_bytes(canonical(cfg) + b"\n")
            body["files"][control.relative_to(root).as_posix()] = dict(
                sha256=file_sha(control), bytes=control.stat().st_size
            )
        else:
            extra = root / "tables/extra-empty.parquet"
            pq.write_table(pq.read_table(target).slice(0, 0), extra)
            target = extra
        body["files"][target.relative_to(root).as_posix()] = dict(
            sha256=file_sha(target), bytes=target.stat().st_size
        )
        (root / "manifest.json").write_bytes(canonical(body) + b"\n")
        sha = file_sha(root / "manifest.json")
        (root / "READY").write_text(sha + "\n")
    with (
        Budget(tmp_path / "scratch", [root], Limits(free_floor_bytes=1)) as budget,
        pytest.raises((RuntimeError, ValueError, OSError)),
    ):
        verify_snapshot_scope(root, manifest_sha256=sha, budget=budget)
