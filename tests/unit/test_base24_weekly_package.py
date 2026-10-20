"""Real local-closure reader and reproduction, including fully repacked tampering."""

import json
import shutil
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
from quanthus.pipelines.securities_warehouse.base24_weekly_package import (
    seal_financial_weekly,
    verify_financial_weekly,
)
from tests.unit.test_base24_weekly_financial import OBSERVED, THROUGH, weekly_config
from tests.unit.test_base24_weekly_materialize import fixture
from tests.unit.test_native_base24_reader import package, reseal


def product(tmp_path, provider_revision="v1"):
    inputs = fixture(tmp_path)
    root, manifest, mat, cfg = package(tmp_path)
    start = datetime(1990, 1, 1, tzinfo=timezone.utc)
    common = dict(effective_from=start, effective_to=None, knowledge_from=start, security_id="s")
    rows = {
        "dwd_security": [{**common, "company_id": "c"}],
        "dwd_security_identifier": [
            {**common, "identifier_namespace": "CN_STOCK_CODE:SZSE", "identifier_value": "000001"}
        ],
    }
    for table, values in rows.items():
        rel = f"payload/{table}/all/part-000000.parquet"
        p = root / rel
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
    reseal(root, manifest, mat, cfg)
    contract = weekly_config()
    contract["layers"] = cfg["layers"]
    contract["financial_statement_source"]["provider_revision"] = provider_revision
    control = tmp_path / "datasets.json"
    control.write_text(
        json.dumps(
            {
                "datasets": [
                    dict(
                        name="financial_statements",
                        status="ready",
                        row_count=3,
                        sha256=inputs["source_sha256"],
                        watermark=THROUGH.isoformat(),
                        source_revision="synthetic-dataset-release-20261017-v2",
                    )
                ]
            }
        )
    )
    limits = Limits(memory_bytes=128 * 1024**2, allocated_bytes=128 * 1024**2, free_floor_bytes=1)
    with Budget(
        tmp_path / "build", [root, inputs["source"], inputs["receipts"], control], limits
    ) as b:
        result = seal_financial_weekly(
            base=root,
            config=contract,
            source=inputs["source"],
            receipts=inputs["receipts"],
            source_sha256=inputs["source_sha256"],
            receipt_sha256=inputs["receipt_sha256"],
            source_control=control,
            source_control_sha256=file_sha(control),
            expected_envelopes=3,
            observed_at=OBSERVED,
            through=THROUGH,
            code_revision="a" * 40,
            budget=b,
            output=b.root / "product",
        )
    return b.root / "product", result, limits, [root, inputs["source"], inputs["receipts"], control]


def test_self_contained_two_moves_and_source_independent_reproduction(tmp_path):
    root, result, limits, originals = product(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["original_dataset_revision"] == "synthetic-dataset-release-20261017-v2"
    assert manifest["original_provider_revision"] == "v1"
    first, second = tmp_path / "move1", tmp_path / "move2"
    shutil.copytree(root, first)
    shutil.copytree(first, second)
    for p in originals:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    outcomes = []
    for i, moved in enumerate([first, second]):
        with Budget(tmp_path / f"verify-{i}", [moved], limits) as b:
            outcomes.append(
                verify_financial_weekly(moved, manifest_sha256=result["manifest_sha256"], budget=b)
            )
        assert all(p.stat().st_nlink == 1 for p in moved.rglob("*") if p.is_file())
    assert outcomes[0] == outcomes[1]
    assert outcomes[0]["status"] == "PASS_FINANCIAL_DOMAIN_ONLY"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "corrupt",
        "extra-repacked",
        "fact-repacked",
        "role-repacked",
        "control-repacked",
        "revision-repacked",
        "provider-repacked",
    ],
)
def test_corruption_and_fully_repacked_semantic_tampering_fail_closed(tmp_path, mutation):
    root, result, limits, _ = product(tmp_path)
    fact = root / "tables/dwd_financial_fact.parquet"
    if mutation == "missing":
        fact.unlink()
    elif mutation == "corrupt":
        fact.write_bytes(b"damaged synthetic bytes")
    else:
        manifest = json.loads((root / "manifest.json").read_text())
        if mutation == "extra-repacked":
            extra = root / "tables/extra-empty.parquet"
            pq.write_table(pq.read_table(fact).slice(0, 0), extra)
            manifest["files"][extra.relative_to(root).as_posix()] = dict(
                sha256=file_sha(extra), bytes=extra.stat().st_size
            )
        elif mutation == "fact-repacked":
            typed = pq.read_table(fact)
            values = typed.to_pylist()
            values[0]["value_text"] = "tampered"
            pq.write_table(pa.Table.from_pylist(values, schema=typed.schema), fact)
            manifest["files"][fact.relative_to(root).as_posix()] = dict(
                sha256=file_sha(fact), bytes=fact.stat().st_size
            )
        elif mutation == "role-repacked":
            cfg = root / "weekly-config.json"
            body = json.loads(cfg.read_text())
            body["purpose"] = "production_execution"
            cfg.write_bytes(canonical(body) + b"\n")
            manifest["files"][cfg.name] = dict(sha256=file_sha(cfg), bytes=cfg.stat().st_size)
        elif mutation == "provider-repacked":
            source = root / "sources/financial_statements.parquet"
            typed = pq.read_table(source)
            values = typed.to_pylist()
            values[0]["source_revision"] = "wrong-provider-revision"
            pq.write_table(pa.Table.from_pylist(values, schema=typed.schema), source)
            manifest["files"]["sources/financial_statements.parquet"] = dict(
                sha256=file_sha(source), bytes=source.stat().st_size
            )
            control = root / "sources/datasets.json"
            body = json.loads(control.read_text())
            body["datasets"][0]["sha256"] = file_sha(source)
            control.write_bytes(canonical(body) + b"\n")
            manifest["files"]["sources/datasets.json"] = dict(
                sha256=file_sha(control), bytes=control.stat().st_size
            )
        else:
            control = root / "sources/datasets.json"
            body = json.loads(control.read_text())
            body["datasets"][0][
                "status" if mutation == "control-repacked" else "source_revision"
            ] = "failed" if mutation == "control-repacked" else "unapproved-revision"
            control.write_bytes(canonical(body) + b"\n")
            manifest["files"]["sources/datasets.json"] = dict(
                sha256=file_sha(control), bytes=control.stat().st_size
            )
        (root / "manifest.json").write_bytes(canonical(manifest) + b"\n")
        result["manifest_sha256"] = file_sha(root / "manifest.json")
        (root / "READY").write_text(result["manifest_sha256"] + "\n")
    with (
        Budget(tmp_path / "verify", [root], limits) as b,
        pytest.raises(
            (RuntimeError, ValueError, FileNotFoundError),
            match="SOURCE_REVISION_DIFFERS" if mutation == "provider-repacked" else None,
        ),
    ):
        verify_financial_weekly(root, manifest_sha256=result["manifest_sha256"], budget=b)


def test_build_rejects_provider_revision_mismatch_and_preserves_originals(tmp_path):
    with pytest.raises(RuntimeError, match="SOURCE_REVISION_DIFFERS"):
        product(tmp_path, provider_revision="wrong-provider-revision")
    assert not (tmp_path / "build/product/READY").exists()
    assert (
        pq.read_table(tmp_path / "source.parquet").column("source_revision").to_pylist()
        == ["v1"] * 3
    )
