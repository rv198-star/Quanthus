"""Regression: fixed acquisition clock and source-backed earlier identities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.base23_identity_refresh import build_refresh
from quanthus.pipelines.securities_warehouse.daily_core_domain import _lineage
from quanthus.pipelines.securities_warehouse.listed_company import _table_schemas

BUSINESS_KEYS = {
    "dwd_listed_company": ["company_id", "effective_from"],
    "dwd_company_identifier": ["identifier_namespace", "identifier_value", "effective_from"],
    "dwd_security": ["security_id", "effective_from"],
    "dwd_security_identifier": ["identifier_namespace", "identifier_value", "effective_from"],
    "dwd_listing": ["listing_id", "effective_from"],
}


def _listing_row(known: datetime) -> dict[str, object]:
    return {
        "record_source": "cninfo.official_ipo+baostock.basic",
        "source_record_id": "notice-1",
        "source_revision": "p10-base231-official-new-listing-daily-observations-v1",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "knowledge_from": known,
        "knowledge_to": None,
        "effective_from": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "effective_to": None,
        "listing_id": "listing_001232",
        "security_id": "security_001232",
        "venue_id": "venue_szse",
        "listing_code": "001232",
        "board_code": "SZ_main",
        "listing_currency": "CNY",
        "lot_size": None,
    }


def _fixture(tmp_path, known: datetime):
    parents = tmp_path / "parents"
    parents.mkdir()
    schema = _table_schemas()["dwd_listing"]
    pq.write_table(
        pa.Table.from_pylist([_listing_row(known)], schema=schema),
        parents / "dwd_listing.parquet",
    )
    master = tmp_path / "security_master.parquet"
    pq.write_table(pa.table({"stock_code": ["001232", "600000"]}), master)
    return {"dwd_listing": [parents / "dwd_listing.parquet"]}, master


def test_refresh_mints_one_earlier_version_of_the_identical_fact(tmp_path):
    observed = datetime(2026, 10, 13, 19, 13, 15, tzinfo=timezone.utc)
    parents, master = _fixture(tmp_path, observed + timedelta(hours=11))
    result = build_refresh(
        master=master,
        observed_at=observed,
        parents=parents,
        business_keys=BUSINESS_KEYS,
        output=tmp_path / "refresh",
        source_sha256="sha256:" + "b" * 64,
        source_revision="security_master@market-daily-provider-20261013-v4",
    )
    assert set(result) == {"dwd_listing"}
    row = pq.ParquetFile(result["dwd_listing"]).read().to_pylist()[0]
    original = _listing_row(observed + timedelta(hours=11))
    assert row["knowledge_from"] == observed
    assert row["knowledge_to"] == original["knowledge_from"]
    assert row["record_source"] == "ref_security_master"
    assert row["source_artifact_sha256"] == "sha256:" + "b" * 64
    for name in (
        "listing_id",
        "security_id",
        "venue_id",
        "listing_code",
        "board_code",
        "effective_from",
        "effective_to",
        "lot_size",
        "listing_currency",
    ):
        assert row[name] == original[name]


def test_refresh_skips_identities_the_warehouse_already_knew_earlier(tmp_path):
    observed = datetime(2026, 10, 13, 19, 13, 15, tzinfo=timezone.utc)
    parents, master = _fixture(tmp_path, observed - timedelta(days=1))
    assert (
        build_refresh(
            master=master,
            observed_at=observed,
            parents=parents,
            business_keys=BUSINESS_KEYS,
            output=tmp_path / "refresh",
            source_sha256="sha256:" + "b" * 64,
            source_revision="security_master@market-daily-provider-20261013-v4",
        )
        == {}
    )


def test_lineage_binds_the_release_artifact_clock_not_the_row_arrival_time():
    cutoff = datetime(2026, 10, 13, 13, 21, 16, tzinfo=timezone.utc)
    artifact = SimpleNamespace(
        source_revision="market-daily-provider-20261013-v4",
        sha256="sha256:" + "c" * 64,
        knowledge_cutoff=cutoff,
    )
    sql = _lineage("unused.parquet", artifact, "o")
    assert cutoff.isoformat() in sql
    assert "acquired_at" not in sql
    assert "o.source_record_id" in sql
