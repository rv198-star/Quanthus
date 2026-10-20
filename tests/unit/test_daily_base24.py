from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import daily_base24 as product
from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha, schemas
from quanthus.pipelines.securities_warehouse.base24_image import KIND as BASE_KIND
from quanthus.pipelines.securities_warehouse.daily_core_domain import _TABLE_KEYS
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
    _daily_core_quality_sha256,
)
from tests.unit.test_native5m_daily_source import original as original

ROOT = Path(__file__).resolve().parents[2]


def seal(root, manifest):
    files = [
        {
            "path": p.relative_to(root).as_posix(),
            "sha256": file_sha(p),
            "size_bytes": p.stat().st_size,
        }
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in {"manifest.json", "READY"}
    ]
    import hashlib

    manifest["files"] = files
    manifest["content_digest"] = "sha256:" + hashlib.sha256(canonical(files)).hexdigest()
    (root / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")


@pytest.fixture
def inputs(original, tmp_path):
    native, _, _, sources = original
    core = tmp_path / "core"
    core.mkdir()
    cfg = json.loads((ROOT / "config/pipelines/base24-daily-governance.v1.json").read_text())
    config = tmp_path / "governance.json"
    config.write_text(json.dumps(cfg))
    base = {
        **cfg["layers"]["dwd"],
        "kind": BASE_KIND,
        "business_through": "2026-09-30",
        "source_consumer_admission": "NOT_GRANTED",
        "governance_use": "governance_baseline",
    }
    base.pop("format")
    model = schemas(
        json.loads((ROOT / "config/pipelines/dwd-domain-model.v1.json").read_text()),
        json.loads((ROOT / "config/pipelines/dwd-domain-model.v1.1-extension.json").read_text()),
    )
    start = datetime(2026, 10, 7, 16, tzinfo=timezone.utc)
    end = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
    known = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    line = {
        "record_source": "synthetic",
        "source_record_id": "x.parquet#code=000001&date=2026-10-08",
        "source_revision": "synthetic-v1",
        "source_artifact_sha256": sources["market_daily_core"],
        "knowledge_from": known,
        "knowledge_to": None,
        "observation_start": start,
        "observation_end": end,
        "listing_id": "listing1",
        "calendar_session_id": "session1",
    }
    bar = {
        **line,
        "interval_size": "1d",
        "price_basis": "none",
        "currency": "CNY",
        **{k: Decimal("2.0000000001") for k in ["open", "high", "low", "close"]},
        "volume": Decimal("4800"),
        "turnover": Decimal("9600"),
        "trade_count": None,
    }
    states = []
    for i in [1, 2]:
        states.append(
            {
                **line,
                "source_record_id": f"x.parquet#code=00000{i}&date=2026-10-08",
                "listing_id": f"listing{i}",
                "is_tradable": i == 1,
                "can_buy": i == 1,
                "can_sell": i == 1,
                "reason_code": "NORMAL" if i == 1 else "SOURCE_NOT_TRADABLE",
                "upper_price": None,
                "lower_price": None,
                "input_revision_digest": "sha256:" + "f" * 64,
            }
        )
    counts = {}
    for table in _TABLE_KEYS:
        p = core / "payload/test" / table / "market_date=2026-10-08/part-00000.parquet"
        p.parent.mkdir(parents=True)
        if table == "dwd_security_bar":
            t = pa.Table.from_pylist([bar], schema=model[table])
        elif table == "dwd_tradability_state":
            t = pa.Table.from_pylist(states, schema=model[table])
        elif table == "dwd_trading_calendar":
            t = pa.table({"fixture": ["calendar"]})
        else:
            t = pa.Table.from_pylist([], schema=model[table])
        pq.write_table(t, p)
        counts[table] = t.num_rows
    (core / "audit.json").write_text(
        json.dumps({"result": "accepted", "market_date": "2026-10-08"})
    )
    m = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_increment",
        "package_id": "synthetic-core",
        "market_date": "2026-10-08",
        "product_scope": "daily-core-v1",
        "quality_policy": "p10-business-value-warehouse-quality.v1",
        "quality_policy_sha256": _daily_core_quality_sha256(),
        "consumable_intervals": ["1d"],
        "selected_releases": {k: {"manifest_sha256": v} for k, v in sources.items()},
        "unavailable_products": {
            "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
            "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
        },
        "base": base,
        "code_revision": "d" * 40,
        "catalog_sha256": "sha256:" + "e" * 64,
        "prior": None,
        "purpose": "isolated_daily_governance",
        "execution_authorized": False,
        "consumer_admission": "NOT_GRANTED",
        "row_counts": counts,
        "acquisition_universe": {"codes": ["000001", "000002"]},
    }
    m["job_identity"] = {
        "base": base,
        "catalog": m["catalog_sha256"],
        "code": m["code_revision"],
        "config": file_sha(config),
        "prior": None,
    }
    seal(core, m)
    return {
        "core": core,
        "native5m": native,
        "config": config,
        "output": tmp_path / "output",
        "code_revision": "f" * 40,
    }


def test_independent_1d5m_with_exact_decimal_and_conservative_clocks(inputs):
    result = product.build(**inputs)
    assert result["row_counts"] == {"1d": 1, "5m": 48}
    assert result["consumer_admission"] == "NOT_GRANTED"
    assert product.verify(inputs["output"]) == result
    rows = pq.ParquetFile(inputs["output"] / "payload/5m.parquet").read().to_pylist()
    assert all(row["close"] == Decimal("2.0000000001") for row in rows)
    assert rows[0]["observation_start"] == datetime(2026, 10, 8, 1, 30, tzinfo=timezone.utc)
    assert all(row["knowledge_from"] == datetime(2026, 10, 9, tzinfo=timezone.utc) for row in rows)
    assert {p.stat().st_nlink for p in inputs["output"].rglob("*") if p.is_file()} == {1}
    before = (inputs["output"] / "manifest.json").read_bytes()
    assert product.build(**inputs) == result
    assert (inputs["output"] / "manifest.json").read_bytes() == before
    with pytest.raises(ValueError, match="IDENTITY"):
        product.build(**{**inputs, "code_revision": "a" * 40})
    assert (inputs["output"] / "manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "mutation", ["missing", "corrupt", "repacked-5m", "repacked-1d", "extra", "hardlink", "symlink"]
)
def test_disposable_successor_fails_closed(inputs, mutation, tmp_path):
    product.build(**inputs)
    root = inputs["output"]
    p = root / "payload/5m.parquet"
    if mutation == "missing":
        p.unlink()
    elif mutation == "corrupt":
        p.write_bytes(b"corrupt")
    elif mutation in {"repacked-5m", "repacked-1d"}:
        p = root / "payload" / ("5m.parquet" if mutation == "repacked-5m" else "1d.parquet")
        t = pq.ParquetFile(p).read()
        rows = t.to_pylist()
        rows[0]["close"] = Decimal("2.0000000002")
        pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
        seal(root, json.loads((root / "manifest.json").read_text()))
    elif mutation == "extra":
        (root / "payload/extra.parquet").write_bytes(b"extra")
    elif mutation == "hardlink":
        import os

        os.link(p, tmp_path / "outside")
    else:
        p.rename(tmp_path / "outside")
        p.symlink_to(tmp_path / "outside")
    with pytest.raises((ValueError, RuntimeError, FileNotFoundError)):
        product.verify(root)


@pytest.mark.parametrize("where", ["source", "staging", "ancestor"])
def test_output_preflight_never_changes_inputs(inputs, where):
    before = {
        p.relative_to(inputs["core"]).as_posix(): p.read_bytes()
        for p in inputs["core"].rglob("*")
        if p.is_file()
    }
    if where == "source":
        inputs["output"] = inputs["core"]
    elif where == "ancestor":
        inputs["output"] = inputs["core"].parent
    else:
        inputs["output"].with_name("." + inputs["output"].name + ".staging").symlink_to(
            inputs["core"], target_is_directory=True
        )
    with pytest.raises((ValueError, RuntimeError)):
        product.build(**inputs)
    assert {
        p.relative_to(inputs["core"]).as_posix(): p.read_bytes()
        for p in inputs["core"].rglob("*")
        if p.is_file()
    } == before


def test_failed_materialization_keeps_evidence_without_ready(inputs, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("disposable injected copy failure")

    monkeypatch.setattr(product.shutil, "copytree", fail)
    with pytest.raises(OSError):
        product.build(**inputs)
    stage = inputs["output"].with_name("." + inputs["output"].name + ".staging")
    assert (stage / "FAILURE.safe.json").is_file() and not (stage / "READY").exists()
    assert not inputs["output"].exists()


def test_build_reuse_rejects_changed_copied_source(inputs, monkeypatch):
    copy = product.shutil.copytree
    original = (inputs["native5m"] / "ods/raw/000001.json.gz").read_bytes()

    def changed_copy(source, destination, *args, **kwargs):
        result = copy(source, destination, *args, **kwargs)
        if Path(destination).name == "native5m":
            (Path(destination) / "ods/raw/000001.json.gz").write_bytes(b"injected copy drift")
        return result

    monkeypatch.setattr(product.shutil, "copytree", changed_copy)
    with pytest.raises(ValueError, match="COPIED_SOURCE_CLOSURE"):
        product.build(**inputs)
    stage = inputs["output"].with_name("." + inputs["output"].name + ".staging")
    assert (stage / "FAILURE.safe.json").is_file() and not (stage / "READY").exists()
    assert (inputs["native5m"] / "ods/raw/000001.json.gz").read_bytes() == original
