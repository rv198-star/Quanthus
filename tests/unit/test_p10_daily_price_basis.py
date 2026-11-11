from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import daily_price_basis as basis
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import build_daily_ods_catalog
from quanthus.pipelines.securities_warehouse.daily_price_basis_domain import (
    DailyDomainBuildError,
    advance_daily_current,
    build_daily_domain_increment,
    verify_daily_domain_increment,
)


def sha(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path):
    """Real sealed four-lane/catalog/Base interfaces, exclusively synthetic rows."""
    c = duckdb.connect()
    releases, base = tmp_path / "releases", tmp_path / "base"
    base.mkdir()
    paths = {}

    def parquet(root, name, query):
        p = root / (name + ".parquet")
        p.parent.mkdir(parents=True, exist_ok=True)
        c.execute(f"COPY ({query}) TO '{p}' (FORMAT PARQUET)")
        return p

    common = """'000001'::VARCHAR stock_code, DATE '2026-08-03' date,
        DATE '2026-08-03' market_date, TIMESTAMPTZ '2026-08-04 01:00:00+00' acquired_at"""
    values = """1.0000000001::DECIMAL(38,10) open, 2::DECIMAL(38,10) high,
        1::DECIMAL(38,10) low, 1.5::DECIMAL(38,10) "close",
        100::DECIMAL(38,10) volume, 150::DECIMAL(38,10) amount"""
    lanes = {
        "market_daily_core": (
            "daily",
            "hermes-market-daily-core-",
            {
                "daily_ohlcv_none_delta": f"SELECT {common}, {values}",
                "daily_status_observation_delta": f"SELECT {common}, '1' tradestatus, '0' isST",
                "valuation_daily_delta": f"SELECT {common}, 1 pe_ttm, 2 pb, 3 ps_ttm, 4 pcf_ttm",
                "index_daily_delta": f"SELECT {common}, {values}, 'idx' index_code",
                "trading_calendar": """SELECT 'calendar' AS source, 'SSE' exchange,
                DATE '2026-08-03' trade_date, 1 is_open,
                TIMESTAMPTZ '2026-08-04 01:00:00+00' built_at""",
                "adjustment_evidence_delta": "SELECT 1 x WHERE false",
            },
        ),
        "intraday_1m": (
            "daily/1m",
            "hermes-intraday-archive-1m-",
            {
                "intraday_bars": f"SELECT {common}, {values}, TIMESTAMP '2026-08-03 09:31:00' ts",
            },
        ),
        "intraday_5m": (
            "daily/5m",
            "hermes-intraday-archive-5m-",
            {
                "intraday_bars": f"SELECT {common}, {values}, TIMESTAMP '2026-08-03 09:35:00' ts",
            },
        ),
        "calendar_daily_events": (
            "daily",
            "hermes-calendar-daily-events-",
            {
                "announcements_delta": f"""SELECT {common}, 'ann1' ann_id, 'title' title,
                'https://example.invalid/synthetic' url, DATE '2026-08-03' announce_date,
                TIMESTAMP '2026-08-03 16:00:00' publish_time""",
                **dict.fromkeys(
                    (
                        "financial_disclosures_delta",
                        "suspensions_delta",
                        "corporate_actions_delta",
                        "dividends_delta",
                        "factor_evidence_delta",
                    ),
                    "SELECT 1 x WHERE false",
                ),
            },
        ),
    }
    for lane, (folder, prefix, queries) in lanes.items():
        root = releases / folder / "2026-08-03" / (prefix + "fixture")
        artifacts = []
        for name, query in queries.items():
            p = parquet(root / "artifacts", name, query)
            paths[(lane, name)] = p
            artifacts.append(
                {
                    "name": name,
                    "relative_path": "artifacts/" + p.name,
                    "sha256": sha(p),
                    "size_bytes": p.stat().st_size,
                    "source_revision": "synthetic@fixture-v1",
                    "knowledge_cutoff": "2026-08-04T01:00:00+00:00",
                }
            )
        manifest = {
            "schema_version": 1,
            "release_id": prefix + "fixture",
            "schedule": "daily",
            "market_date": "2026-08-03",
            "created_at": "2026-08-04T01:00:00+00:00",
            "producer": "synthetic",
            "status": "ready",
            "artifacts": artifacts,
            "schedule_contract_id": "synthetic-schedule",
            "schedule_contract_revision": "v1",
            "schedule_config_sha256": "sha256:" + "a" * 64,
        }
        (root / "release.json").write_text(json.dumps(manifest))
        (root / "READY").write_text(sha(root / "release.json") + "\n")
    base_queries = {
        "dwd_listing": """SELECT 'listing-a' listing_id,'security-a' security_id,'venue-a' venue_id,
            '000001' listing_code,'CNY' listing_currency,
            TIMESTAMPTZ '2020-01-01 00:00:00+00' effective_from,NULL::TIMESTAMPTZ effective_to""",
        "dwd_security": """SELECT 'security-a' security_id,'company-a' company_id,
            TIMESTAMPTZ '2020-01-01 00:00:00+00' effective_from,NULL::TIMESTAMPTZ effective_to""",
        "dwd_trading_calendar": "SELECT 'unused' calendar_session_id",
        "dwd_market_index": "SELECT 'idx' index_code,'index-a' index_id,'CNY' base_currency",
        "dwd_trading_venue": "SELECT 'venue-a' venue_id,'XSHG' mic_code",
    }
    files = []
    for name, query in base_queries.items():
        p = parquet(base / "payload", name, query)
        files.append(
            {"path": "payload/" + p.name, "sha256": sha(p), "size_bytes": p.stat().st_size}
        )
    manifest = {
        "kind": "dwd_domain_base_package",
        "package_id": "synthetic-base",
        "content_digest": "sha256:" + "b" * 64,
        "files": files,
    }
    (base / "manifest.json").write_text(json.dumps(manifest))
    (base / "READY").write_text(sha(base / "manifest.json") + "\n")
    config = tmp_path / "release.json"
    config.write_text(
        json.dumps(
            {
                "through_date": "2026-07-31",
                "build_id": "synthetic",
                "layers": {
                    "dwd": {
                        "package_id": manifest["package_id"],
                        "manifest_sha256": sha(base / "manifest.json"),
                        "content_digest": manifest["content_digest"],
                    }
                },
            }
        )
    )
    catalog = tmp_path / "catalog"
    build_daily_ods_catalog(release_root=releases, market_date=date(2026, 8, 3), output=catalog)
    c.close()
    return {
        "catalog_root": catalog,
        "release_root": releases,
        "base_root": base,
        "release_config": config,
        "code_revision": "c" * 40,
    }


def bar(package):
    return next(package.glob("payload/security_market_observation/dwd_security_bar/**/*.parquet"))


def test_producer_opt_in_preserves_legacy_and_blocks_default_consumers(inputs, tmp_path):
    old, new = tmp_path / "legacy", tmp_path / "candidate"
    build_daily_domain_increment(**inputs, output=old)
    result = build_daily_domain_increment(**inputs, output=new, price_basis_policy=basis.POLICY)
    before, after = pq.ParquetFile(bar(old)).read(), pq.ParquetFile(bar(new)).read()
    assert before.schema == after.schema
    assert set(before["price_basis"].to_pylist()) == {"none"}
    rows = after.to_pylist()
    assert {r["price_basis"] for r in rows if r["interval_size"] == "1d"} == {"UNADJUSTED"}
    assert {r["price_basis"] for r in rows if r["interval_size"] != "1d"} == {"none"}
    assert all(r["knowledge_from"].date() == date(2026, 8, 4) for r in rows)
    assert result == verify_daily_domain_increment(new, price_basis_policy=basis.POLICY)
    audit = json.loads((new / "audit.json").read_text())["price_basis"]
    assert audit["equality"]["source_differences"] == 0
    assert audit["coverage"]["canonical_daily_rows"] == 1
    assert audit["coverage"]["raw_known_after_close_rows"] == 1
    assert audit["coverage"]["decision_admissible"] is False
    assert audit["historical_replacement_comparison"] == "NOT_PERFORMED"
    with pytest.raises(DailyDomainBuildError, match="EXPLICIT_ADMISSION"):
        verify_daily_domain_increment(new)
    with pytest.raises(DailyDomainBuildError, match="EXPLICIT_ADMISSION"):
        advance_daily_current(package=new, pointer=tmp_path / "CURRENT")
    assert not (tmp_path / "CURRENT").exists()
    with pytest.raises(DailyDomainBuildError, match="REQUESTED_CONTRACT_DIFFERS"):
        build_daily_domain_increment(**inputs, output=old, price_basis_policy=basis.POLICY)


def test_unknown_policy_fails_before_output(inputs, tmp_path):
    with pytest.raises(ValueError, match="POLICY_UNKNOWN"):
        build_daily_domain_increment(**inputs, output=tmp_path / "bad", price_basis_policy="qfq")
    assert not (tmp_path / "bad").exists()


def test_default_v1_output_is_byte_identical_to_pinned_legacy_builder(inputs, tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "fixtures/p10/daily_domain.3382b63.py.txt"
    assert sha(fixture) == (
        "sha256:9dd94a250782a259701a030cb9a1b6a6e2591ad382ec12bf478fe3b609a06368"
    )
    executable = tmp_path / "legacy_daily_domain.py"
    executable.write_bytes(fixture.read_bytes())
    spec = importlib.util.spec_from_file_location("p10_pinned_legacy_daily_domain", executable)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        legacy, current = tmp_path / "legacy-runtime", tmp_path / "current-default"
        module.build_daily_domain_increment(**inputs, output=legacy)
        build_daily_domain_increment(**inputs, output=current)
        before = {
            p.relative_to(legacy).as_posix(): sha(p) for p in legacy.rglob("*") if p.is_file()
        }
        after = {
            p.relative_to(current).as_posix(): sha(p) for p in current.rglob("*") if p.is_file()
        }
        assert before == after
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture
def source(inputs, tmp_path):
    root = tmp_path / "legacy"
    build_daily_domain_increment(**inputs, output=root)
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE raw AS SELECT * FROM read_parquet(?,hive_partitioning=false)",
        [str(bar(root))],
    )
    revision, digest = c.execute(
        "SELECT source_revision,source_artifact_sha256 FROM raw WHERE interval_size='1d'"
    ).fetchone()
    yield c, SimpleNamespace(source_revision=revision, sha256=digest)
    c.close()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("UPDATE raw SET price_basis='qfq' WHERE interval_size='1d'", "BASIS_OR_TIME"),
        ("UPDATE raw SET price_basis=NULL WHERE interval_size='1d'", "BASIS_OR_TIME"),
        ("UPDATE raw SET knowledge_from=NULL WHERE interval_size='1d'", "BASIS_OR_TIME"),
        (
            "UPDATE raw SET observation_end=observation_start WHERE interval_size='1d'",
            "BASIS_OR_TIME",
        ),
        (
            "UPDATE raw SET source_artifact_sha256='changed' WHERE interval_size='1d'",
            "BASIS_OR_TIME",
        ),
        ("UPDATE raw SET source_revision='changed' WHERE interval_size='1d'", "BASIS_OR_TIME"),
        ('ALTER TABLE raw ALTER COLUMN "close" TYPE DOUBLE', "SCHEMA_UNKNOWN"),
        ("ALTER TABLE raw ADD COLUMN inferred BOOLEAN", "SCHEMA_UNKNOWN"),
        ("UPDATE raw SET high=0 WHERE interval_size='1d'", "PRICE_INVALID"),
    ],
)
def test_mapping_rejects_unknown_or_changed_source(source, mutation, reason):
    c, artifact = source
    c.execute(mutation)
    with pytest.raises(ValueError, match=reason):
        basis.prepare(c, "SELECT * FROM raw", artifact)


@pytest.mark.parametrize(
    "field,value",
    [
        ("close", "1.5000000001"),
        ("knowledge_from", "'2026-08-03 06:00:00+00'"),
        ("source_artifact_sha256", "'changed'"),
        ("price_basis", "NULL"),
    ],
)
def test_independent_equality_rejects_serialization_drift(source, tmp_path, field, value):
    c, artifact = source
    query = basis.prepare(c, "SELECT * FROM raw", artifact)
    c.execute("CREATE TABLE candidate AS " + query)
    c.execute(f"UPDATE candidate SET \"{field}\"={value} WHERE interval_size='1d'")
    output = tmp_path / "mutated.parquet"
    c.execute(f"COPY candidate TO '{output}' (FORMAT PARQUET)")
    with pytest.raises(ValueError, match="DRIFT|DIFFERS"):
        basis.verify_preservation(c, output)


def test_exact_decimal_scale_and_optional_nulls_preserved(source, tmp_path):
    c, artifact = source
    c.execute("UPDATE raw SET volume=NULL,turnover=NULL WHERE interval_size='1d'")
    query = basis.prepare(c, "SELECT * FROM raw", artifact)
    output = tmp_path / "nulls.parquet"
    c.execute(f"COPY ({query}) TO '{output}' (FORMAT PARQUET)")
    assert basis.verify_preservation(c, output)["source_differences"] == 0
    assert pq.ParquetFile(output).schema_arrow.field("close").type == pa.decimal128(38, 10)


def test_machine_contract_matches_implementation():
    path = Path(__file__).resolve().parents[2] / "config/pipelines/daily-price-basis.v1.json"
    assert json.loads(path.read_text())["contract"] == basis.contract(basis.POLICY)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE raw SET calendar_session_id='missing' WHERE interval_size='1d'",
        "UPDATE raw SET observation_start=observation_start+INTERVAL 1 HOUR "
        "WHERE interval_size='1d'",
    ],
)
def test_calendar_association_fails_closed(source, tmp_path, mutation):
    c, artifact = source
    root = tmp_path / "legacy"
    calendar = next(root.glob("payload/tradability_state/dwd_trading_calendar/**/*.parquet"))
    status = next(root.glob("payload/tradability_state/dwd_tradability_state/**/*.parquet"))
    c.execute(mutation)
    basis.prepare(c, "SELECT * FROM raw", artifact)
    with pytest.raises(ValueError, match="CALENDAR_SCOPE_INVALID"):
        basis.coverage(c, calendar, status, "2026-08-03")


def test_coverage_retains_status_only_bar_only_and_zero_activity(source, tmp_path):
    c, artifact = source
    root = tmp_path / "legacy"
    calendar = next(root.glob("payload/tradability_state/dwd_trading_calendar/**/*.parquet"))
    status = next(root.glob("payload/tradability_state/dwd_tradability_state/**/*.parquet"))
    c.execute("UPDATE raw SET volume=0,turnover=0 WHERE interval_size='1d'")
    c.execute(
        "CREATE TABLE statuses AS SELECT * FROM read_parquet(?,hive_partitioning=false)",
        [str(status)],
    )
    c.execute("UPDATE statuses SET listing_id='status-only',is_tradable=false")
    census = tmp_path / "status.parquet"
    c.execute(f"COPY statuses TO '{census}' (FORMAT PARQUET)")
    basis.prepare(c, "SELECT * FROM raw", artifact)
    result = basis.coverage(c, calendar, census, "2026-08-03")
    assert result["status_or_bar_pairs"] == 2
    assert result["status_without_bar"] == result["bar_without_status"] == 1
    assert result["source_nontradable_status"] == result["zero_activity_rows"] == 1
    assert result["suspension_classification"] == "NOT_INFERRED"


@pytest.mark.parametrize("change", ["schema", "contract", "missing_contract"])
def test_candidate_metadata_drift_fails_closed(inputs, tmp_path, change):
    root = tmp_path / "candidate"
    build_daily_domain_increment(**inputs, output=root, price_basis_policy=basis.POLICY)
    manifest = json.loads((root / "manifest.json").read_text())
    if change == "schema":
        manifest["schema_version"] = 1
    elif change == "contract":
        manifest["price_basis_contract"]["decision_admissible"] = True
    else:
        del manifest["price_basis_contract"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "READY").write_text(sha(root / "manifest.json") + "\n")
    with pytest.raises(DailyDomainBuildError, match="CONTRACT_DIFFERS"):
        verify_daily_domain_increment(root, price_basis_policy=basis.POLICY)


def test_relabeling_legacy_bytes_never_establishes_candidate(inputs, tmp_path):
    root = tmp_path / "legacy"
    build_daily_domain_increment(**inputs, output=root)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest.update(
        {
            "schema_version": 2,
            "kind": basis.CANDIDATE_KIND,
            "price_basis_contract": basis.contract(basis.POLICY),
        }
    )
    manifest["package_id"] += "-" + basis.POLICY
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "READY").write_text(sha(root / "manifest.json") + "\n")
    with pytest.raises(DailyDomainBuildError, match="ATTESTATION_INVALID"):
        verify_daily_domain_increment(root, price_basis_policy=basis.POLICY)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE candidate SET price_basis='none' WHERE interval_size='1d'",
        "UPDATE candidate SET knowledge_from=NULL WHERE interval_size='1d'",
        "INSERT INTO candidate SELECT * FROM candidate WHERE interval_size='1d'",
    ],
)
def test_resealed_candidate_payload_must_still_match_contract(inputs, tmp_path, mutation):
    root = tmp_path / "candidate"
    build_daily_domain_increment(**inputs, output=root, price_basis_policy=basis.POLICY)
    output = bar(root)
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE candidate AS SELECT * FROM read_parquet(?,hive_partitioning=false)",
        [str(output)],
    )
    c.execute(mutation)
    c.execute(f"COPY candidate TO '{output}' (FORMAT PARQUET)")
    c.close()
    manifest = json.loads((root / "manifest.json").read_text())
    for item in manifest["files"]:
        if item["path"] == output.relative_to(root).as_posix():
            item.update({"sha256": sha(output), "size_bytes": output.stat().st_size})
    serialized = json.dumps(
        manifest["files"], ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    manifest["content_digest"] = "sha256:" + hashlib.sha256(serialized).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "READY").write_text(sha(root / "manifest.json") + "\n")
    with pytest.raises(DailyDomainBuildError, match="BASIS_OR_TIME|IDENTITY_DIFFERS"):
        verify_daily_domain_increment(root, price_basis_policy=basis.POLICY)


def test_physical_timestamp_precision_cannot_be_normalized_away(inputs, tmp_path):
    root = tmp_path / "candidate"
    build_daily_domain_increment(**inputs, output=root, price_basis_policy=basis.POLICY)
    source = pq.ParquetFile(bar(root)).read()
    fields = [
        pa.field(f.name, pa.timestamp("ms", "UTC")) if pa.types.is_timestamp(f.type) else f
        for f in source.schema
    ]
    changed = tmp_path / "milliseconds.parquet"
    pq.write_table(source.cast(pa.schema(fields)), changed)
    with pytest.raises(ValueError, match="PHYSICAL_SCHEMA_DIFFERS"):
        basis.validate_physical_schema(changed)


@pytest.mark.parametrize("timezone", ["UTC", "Etc/UTC", "Asia/Shanghai", "America/New_York"])
@pytest.mark.parametrize("drift", [False, True])
def test_preservation_is_independent_of_session_timezone(source, tmp_path, timezone, drift):
    c, artifact = source
    c.execute("SET TimeZone = ?", [timezone])
    query = basis.prepare(c, "SELECT * FROM raw", artifact)
    c.execute("CREATE TABLE candidate AS " + query)
    if drift:
        c.execute("UPDATE candidate SET knowledge_from=knowledge_from+INTERVAL 1 MICROSECOND")
    output = tmp_path / "timezone.parquet"
    c.execute(f"COPY candidate TO '{output}' (FORMAT PARQUET)")
    before = output.read_bytes()
    source_before = c.execute(
        "SELECT epoch_us(knowledge_from),epoch_us(knowledge_to),"
        "epoch_us(observation_start),epoch_us(observation_end) "
        "FROM p10_basis_source ORDER BY " + basis.ORDER
    ).fetchall()
    if drift:
        with pytest.raises(ValueError, match="SERIALIZED_SOURCE_DRIFT"):
            basis.verify_preservation(c, output)
    else:
        assert basis.verify_preservation(c, output)["source_differences"] == 0
    assert c.execute("SELECT current_setting('TimeZone')").fetchone()[0] == timezone
    assert output.read_bytes() == before
    assert (
        c.execute(
            "SELECT epoch_us(knowledge_from),epoch_us(knowledge_to),"
            "epoch_us(observation_start),epoch_us(observation_end) "
            "FROM p10_basis_source ORDER BY " + basis.ORDER
        ).fetchall()
        == source_before
    )


@pytest.mark.parametrize("process_timezone", ["UTC", "Asia/Shanghai", "America/New_York"])
@pytest.mark.parametrize("session_timezone", ["UTC", "Etc/UTC", "Asia/Shanghai"])
def test_producer_preserves_shanghai_trading_day_across_timezones(
    inputs, tmp_path, monkeypatch, process_timezone, session_timezone
):
    original_connect = duckdb.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.execute("SET TimeZone = ?", [session_timezone])
        return connection

    with monkeypatch.context() as patch:
        patch.setenv("TZ", process_timezone)
        time.tzset()
        patch.setattr(duckdb, "connect", connect)
        try:
            root = tmp_path / "candidate"
            build_daily_domain_increment(**inputs, output=root, price_basis_policy=basis.POLICY)
            rows = pq.ParquetFile(bar(root)).read().to_pylist()
            daily = next(row for row in rows if row["interval_size"] == "1d")
            # Shanghai midnight belongs to August 3, although its UTC date is August 2.
            assert daily["observation_start"] == datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
            assert daily["observation_end"] == datetime(2026, 8, 3, 16, tzinfo=timezone.utc)
            manifest = json.loads((root / "manifest.json").read_text())
            assert manifest["market_date"] == "2026-08-03"
            calendar_path = next(
                root.glob("payload/tradability_state/dwd_trading_calendar/**/*.parquet")
            )
            calendar = pq.ParquetFile(calendar_path).read().to_pylist()[0]
            assert calendar["trade_date"] == date(2026, 8, 3)
            assert calendar["open_at"] == datetime(2026, 8, 3, 1, 30, tzinfo=timezone.utc)
            assert calendar["close_at"] == datetime(2026, 8, 3, 7, tzinfo=timezone.utc)
            # The existing producer explicitly localizes naive source bars to Shanghai.
            minute = next(row for row in rows if row["interval_size"] == "1m")
            assert minute["observation_start"] == datetime(2026, 8, 3, 1, 30, tzinfo=timezone.utc)
            assert minute["observation_end"] == datetime(2026, 8, 3, 1, 31, tzinfo=timezone.utc)
        finally:
            patch.undo()
            time.tzset()


@pytest.mark.parametrize(
    "field", ["knowledge_from", "knowledge_to", "observation_start", "observation_end"]
)
def test_preservation_source_rejects_naive_timestamp_fields(source, field):
    c, artifact = source
    c.execute(f'ALTER TABLE raw ALTER COLUMN "{field}" TYPE TIMESTAMP')
    with pytest.raises(ValueError, match="SOURCE_SCHEMA_UNKNOWN"):
        basis.prepare(c, "SELECT * FROM raw", artifact)
