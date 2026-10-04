from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_exact import (
    CONTRACT_ID,
    TABLE,
    exact_holder_observation,
    holder_evidence,
    load_holder_contract,
    validate_holder_row,
)
from quanthus.pipelines.securities_warehouse.base22_outputs import holder_observation

CONFIG = Path("config/pipelines")
PARENT = datetime(2026, 8, 4, tzinfo=timezone.utc)


def project(value):
    raw = {
        "code": "000001",
        "end_date": "2026-06-30",
        "shareholder_count": "10",
        "avg_hold_num": value,
        "acquired_at": "2026-08-01T19:30:04+08:00",
        "hold_notice_date": "2026-07-25",
    }
    return exact_holder_observation(
        raw,
        company_id="c",
        security_id="s",
        parent_knowledge=PARENT,
        source_sha256="sha256:" + "a" * 64,
        revision="synthetic-new-revision",
        contract_id=CONTRACT_ID,
    )[TABLE][0]


@pytest.mark.parametrize(
    ("value", "old_fits", "new_fits"),
    [
        ("1234.12345678901", False, True),
        ("1234.1234567890", True, True),
        ("1234.12345678900000", True, True),
        ("9999999999999999999999999999", True, False),
        ("0.00000000000", True, True),
        ("-0.00000000000", True, True),
        (None, False, False),
        ("", False, False),
    ],
)
def test_version_specific_exact_columns_and_lossless_serialization(value, old_fits, new_fits):
    contract, schema = load_holder_contract(CONFIG)
    row = project(value)
    assert row["raw_average_holding"] == value
    expected = Decimal(value) if value not in (None, "") else None
    assert row["average_holding"] == (expected if old_fits else None)
    assert row["average_holding_exact"] == (expected if new_fits else None)
    assert row["knowledge_from"] == PARENT and row["published_at"] is None
    assert pa.Table.from_pylist([row], schema=schema).to_pylist() == [row]
    assert validate_holder_row(row) == holder_evidence(row)
    _, model, extension = load_contract(CONFIG)
    legacy = schemas(model, extension)[TABLE]
    assert len(schema) == len(legacy) + 1
    assert all(schema.field(f.name) == f for f in legacy)
    assert contract["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "garbage", 1.25, True, [], "1e39", "1e-12"])
def test_unsupported_values_remain_fail_closed(value):
    with pytest.raises(BoundedError, match="HOLDER_EXACT"):
        project(value)


def test_decimal_context_cannot_round_the_new_or_legacy_exact_scalar():
    with localcontext() as context:
        context.prec = 4
        row = project("123456789012345678901234567.12345678901")
        assert row["average_holding_exact"] == Decimal("123456789012345678901234567.12345678901")
        assert row["average_holding"] is None
        validate_holder_row(row)


def test_existing_default_adapter_remains_strict():
    raw = {
        "code": "000001",
        "end_date": "2026-06-30",
        "shareholder_count": "10",
        "avg_hold_num": "1234.12345678901",
        "acquired_at": PARENT,
    }
    with pytest.raises(Exception, match="HOLDER_DECIMAL_PRECISION_CHANGED"):
        holder_observation(
            raw,
            company_id="c",
            security_id="s",
            parent_knowledge=PARENT,
            source_sha256="hash",
            revision="r",
        )


@pytest.mark.parametrize("fault", ["old_round", "new_round", "parser", "date", "count", "raw"])
def test_row_semantic_rejection_independent_of_packaged_hashes(fault):
    row = project("1234.12345678901")
    if fault == "old_round":
        row["average_holding"] = Decimal("1234.1234567890")
    if fault == "new_round":
        row["average_holding_exact"] = Decimal("1234.12345678900")
    if fault == "parser":
        row["parser_version"] = "legacy"
    if fault == "date":
        row["period_end"] = datetime(2026, 9, 1).date()
    if fault == "count":
        row["holder_count"] = 11
    if fault == "raw":
        row["raw_average_holding"] = "1234.12345678902"
    with pytest.raises(BoundedError):
        validate_holder_row(row)


def test_unknown_or_modified_contract_is_not_a_version_selector(tmp_path):
    import json
    import shutil

    for path in CONFIG.glob("*.json"):
        shutil.copyfile(path, tmp_path / path.name)
    path = tmp_path / "base22-holder-exact.v2.json"
    document = json.loads(path.read_bytes())
    document["field"]["scale"] = 12
    path.write_text(json.dumps(document))
    with pytest.raises(BoundedError, match="HOLDER_EXACT_CONTRACT"):
        load_holder_contract(tmp_path)


def sealed_scope(tmp_path):
    import shutil

    from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha, partition
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base22_outputs import write_partition

    root = tmp_path / "job"
    contract, _, _ = load_contract(CONFIG)
    _, schema = load_holder_contract(CONFIG)
    row = project("1234.12345678901")
    rule = contract["tables"][TABLE]
    with Budget(root, [], Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2)) as budget:
        folder = root / "observations" / "scope"
        config = folder / "contract"
        config.mkdir(parents=True)
        names = [
            "base22-holder-exact.v2.json",
            "base22-independent-image.v1.json",
            "dwd-domain-model.v1.json",
            "dwd-domain-model.v1.1-extension.json",
        ]
        for name in names:
            shutil.copyfile(CONFIG / name, config / name)
        path = folder / "payload" / TABLE / "one.parquet"
        item = write_partition(
            [row],
            table=TABLE,
            expected_rows=1,
            expected_partition=partition(row, rule),
            schema=schema,
            rule=rule,
            admitted={
                (row["record_source"], row["source_revision"], row["source_artifact_sha256"])
            },
            output=path,
            budget=budget,
        )
    item["path"] = str(path.relative_to(root))
    body = {
        "kind": "sealed_actual_domain_observations_not_complete_base22",
        "input_kind": "synthetic",
        "label": "scope",
        "outputs": [item],
        "output_rows": {TABLE: 1},
        "consumer_admission": "NOT_GRANTED",
        "archived_true_source_observations": [],
        "schema_contract_id": CONTRACT_ID,
        "schema_contract_sha256": file_sha(config / "base22-holder-exact.v2.json"),
        "contract_members": {name: file_sha(config / name) for name in names},
        "precision_evidence": [holder_evidence(row)],
        "inputs": [
            {
                "record_source": row["record_source"],
                "revision": row["source_revision"],
                "sha256": row["source_artifact_sha256"],
            }
        ],
    }
    return root, config, path, {**body, "identity": digest(body)}, schema, rule


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "old_round",
        "new_round",
        "evidence",
        "unknown_source",
        "partition",
        "key_census",
        "duplicate",
        "contract",
        "legacy_selector",
        "unknown_selector",
    ],
)
def test_full_repackaged_cold_scope_requires_semantics_and_bound_evidence(tmp_path, fault):
    import json

    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_output_audit import audit_observation_stage

    root, config, path, descriptor, schema, rule = sealed_scope(tmp_path)
    row = pq.read_table(path).to_pylist()[0]
    if fault == "old_round":
        row["average_holding"] = Decimal("1234.1234567890")
    if fault == "new_round":
        row["average_holding_exact"] = Decimal("1234.12345678900")
    if fault == "unknown_source":
        row["record_source"] = "unknown"
    rows = [row]
    if fault == "duplicate":
        rows.append(dict(row, source_record_id="another record, same version key"))
    if fault in {"old_round", "new_round", "unknown_source", "duplicate"}:
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
        descriptor["precision_evidence"] = [holder_evidence(r) for r in rows]
        descriptor["output_rows"][TABLE] = len(rows)
        item = descriptor["outputs"][0]
        item.update(
            sha256=file_sha(path),
            size_bytes=path.stat().st_size,
            rows=len(rows),
            semantic_scan=semantic_scan([path], schema),
            version_key_census_sha256=digest(
                sorted(digest([r[k] for k in rule["version_key"]]) for r in rows)
            ),
        )
    if fault == "evidence":
        descriptor["precision_evidence"][0]["old_unavailable_reason"] = None
    if fault == "partition":
        descriptor["outputs"][0]["partition"] = "period_end_year=2025"
    if fault == "key_census":
        descriptor["outputs"][0]["version_key_census_sha256"] = digest([])
    if fault == "legacy_selector":
        descriptor.pop("schema_contract_id")
    if fault == "unknown_selector":
        descriptor["schema_contract_id"] = "unknown-v99"
    if fault == "contract":
        p = config / "base22-holder-exact.v2.json"
        document = json.loads(p.read_bytes())
        document["purpose"] = "production_current"
        p.write_text(json.dumps(document))
        descriptor["contract_members"][p.name] = file_sha(p)
        descriptor["schema_contract_sha256"] = file_sha(p)
    descriptor["identity"] = digest({k: v for k, v in descriptor.items() if k != "identity"})
    if fault:
        with pytest.raises((BoundedError, RuntimeError)):
            audit_observation_stage(root, descriptor, config)
    else:
        assert audit_observation_stage(root, descriptor, config)["exact_column_semantics"] == "PASS"


def test_two_relocations_and_new_processes_have_only_local_contract_and_payload(tmp_path):
    import json
    import os
    import shutil
    import subprocess
    import sys

    root, _, _, descriptor, _, _ = sealed_scope(tmp_path)
    (root / "descriptor.json").write_text(json.dumps(descriptor))
    reader = (
        "import json,sys;from pathlib import Path;"
        "from quanthus.pipelines.securities_warehouse.base22_output_audit "
        "import audit_observation_stage;"
        "r=Path(sys.argv[1]);d=json.loads((r/'descriptor.json').read_bytes());"
        "assert audit_observation_stage(r,d,r/'observations/scope/contract')"
        "['exact_column_semantics']=='PASS'"
    )
    for index in range(2):
        copied = tmp_path / f"relocated-{index}"
        shutil.copytree(root, copied)
        shutil.rmtree(root)  # disposable synthetic original only
        subprocess.run(
            [sys.executable, "-c", reader, str(copied)],
            check=True,
            env={"PATH": os.environ["PATH"], "PYTHONPATH": str(Path("src").resolve())},
            cwd=tmp_path,
        )
        root = copied


@pytest.mark.parametrize("fault", [None, "raw_hash", "parent_time", "missing_bindings"])
def test_cold_local_raw_reproduction_without_original_sources(tmp_path, fault):
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
    from quanthus.pipelines.securities_warehouse.base22_output_audit import audit_observation_stage

    root, config, path, descriptor, schema, rule = sealed_scope(tmp_path)
    raw = {
        "code": "000001",
        "end_date": "2026-06-30",
        "shareholder_count": "10",
        "avg_hold_num": "1234.12345678901",
        "acquired_at": "2026-08-01T19:30:04+08:00",
        "hold_notice_date": "2026-07-25",
    }
    archive = root / "observations/scope/source-observations/raw.parquet"
    archive.parent.mkdir()
    pq.write_table(pa.Table.from_pylist([raw]), archive)
    row = pq.read_table(path).to_pylist()[0]
    row["source_artifact_sha256"] = file_sha(archive)
    pq.write_table(pa.Table.from_pylist([row], schema=schema), path)
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan

    item = descriptor["outputs"][0]
    item.update(
        sha256=file_sha(path),
        size_bytes=path.stat().st_size,
        semantic_scan=semantic_scan([path], schema),
    )
    descriptor["inputs"][0]["sha256"] = file_sha(archive)
    descriptor["precision_evidence"] = [holder_evidence(row)]
    descriptor["input_kind"] = "retained_raw_governance"
    descriptor["archived_true_source_observations"] = [
        {"path": str(archive.relative_to(root)), "sha256": file_sha(archive), "rows": 1}
    ]
    descriptor["raw_origin_bindings"] = [
        {
            "row_sha256": digest(row),
            "archive_path": str(archive.relative_to(root)),
            "raw_row_index": 0,
            "raw_row_sha256": digest(raw),
            "parent_knowledge_from": PARENT.isoformat(),
        }
    ]
    if fault == "raw_hash":
        descriptor["raw_origin_bindings"][0]["raw_row_sha256"] = digest({})
    if fault == "parent_time":
        descriptor["raw_origin_bindings"][0]["parent_knowledge_from"] = "2026-10-04T00:00:00+00:00"
    if fault == "missing_bindings":
        descriptor.pop("raw_origin_bindings")
    descriptor["identity"] = digest({k: v for k, v in descriptor.items() if k != "identity"})
    if fault:
        with pytest.raises(BoundedError):
            audit_observation_stage(root, descriptor, config)
    else:
        assert audit_observation_stage(root, descriptor, config)["local_raw_reproduction"] == "PASS"
