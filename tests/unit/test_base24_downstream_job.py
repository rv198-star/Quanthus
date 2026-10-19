"""Monthly Base 2.4 projection-to-DWD ownership transfer contracts."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
from quanthus.pipelines.securities_warehouse.base24_downstream_job import (
    Base24DownstreamError,
    audit_global_transfer,
    audit_month_resolution,
    freeze_month_identity,
    record_month_failure,
    retire_projection_tmp,
    seal_global_transfer,
    seal_month_transfer,
    select_month_source_members,
    validate_successor_ledger,
)
from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import BUCKETS
from quanthus.pipelines.securities_warehouse.base24_native5m_projection import seal_projection
from quanthus.pipelines.securities_warehouse.market_observation import _schemas

SHANGHAI = ZoneInfo("Asia/Shanghai")
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def _ledger(
    root: Path,
    *,
    disposition: str = "OBSERVED",
    source_family: str = "new_native_5m",
) -> Path:
    root.mkdir()
    table = pa.Table.from_pylist(
        [
            {
                "venue": "SSE",
                "stock_code": "600000",
                "trade_date": date(2020, 1, 2),
                "disposition": disposition,
                "rating": "R2",
                "reason": "native_48_bucket_grid",
                "evidence_warning": None,
                "source_family": source_family,
                "evidence_digest": HASH_A,
                "first_acquired_at": "2026-10-16T00:00:00+00:00",
                "last_acquired_at": "2026-10-16T00:00:00+00:00",
            }
        ]
    )
    pq.write_table(table, root / "coverage-ledger.parquet")
    _write(
        root / "result.safe.json",
        {"status": "PASS", "R3_security_days": 0, "required_security_days": 1},
    )
    files = [
        {
            "path": name,
            "size_bytes": (root / name).stat().st_size,
            "sha256": file_sha(root / name),
        }
        for name in ("coverage-ledger.parquet", "result.safe.json")
    ]
    _write(
        root / "manifest.json",
        {
            "contract": "p10-base24-native5m-coverage-ledger-package-v1",
            "status": "PASS",
            "files": files,
        },
    )
    (root / "READY").write_text(
        file_sha(root / "manifest.json").removeprefix("sha256:") + "\n", encoding="ascii"
    )
    return root


def _projection_rows() -> list[dict[str, object]]:
    rows = []
    for value in BUCKETS:
        hour, minute, second = map(int, value.split(":"))
        rows.append(
            {
                "stock_code": "600000",
                "ts": datetime(2020, 1, 2, hour, minute, second),
                "market_date": date(2020, 1, 2),
                "bar_size": "5m",
                "source_values": ["1", "1", "1", "1", "0", "0"],
                "open": Decimal("1.0000000000"),
                "high": Decimal("1.0000000000"),
                "low": Decimal("1.0000000000"),
                "close": Decimal("1.0000000000"),
                "volume": Decimal("0E-10"),
                "amount": Decimal("0E-10"),
                "acquired_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
                "input_partition_id": "a" * 64,
                "input_artifact_sha256": HASH_B,
                "observation_year": 2020,
                "observation_month": 1,
            }
        )
    return rows


def _projection(root: Path) -> tuple[Path, pa.Table]:
    root.mkdir(parents=True)
    table = pa.Table.from_pylist(_projection_rows())
    connection = duckdb.connect()
    connection.register("projected", table)
    try:
        connection.execute(
            f"""
            COPY (
              SELECT stock_code,ts::TIMESTAMP ts,market_date,bar_size,source_values,
                     open::DECIMAL(38,10) open,high::DECIMAL(38,10) high,
                     low::DECIMAL(38,10) low,"close"::DECIMAL(38,10) "close",
                     volume::DECIMAL(38,10) volume,amount::DECIMAL(38,10) amount,
                     acquired_at::TIMESTAMPTZ acquired_at,input_partition_id,
                     input_artifact_sha256,observation_year,observation_month
              FROM projected
            ) TO '{root / "payload"}'
            (FORMAT PARQUET,PARTITION_BY(observation_year,observation_month),COMPRESSION ZSTD)
            """
        )
    finally:
        connection.close()
    _write(root / "REGISTRATION.json", {"contract": "test-monthly-projection"})
    seal_projection(root, input_identity={"ledger_manifest_sha256": HASH_A}, code_revision="test")
    return root, table


def _supplement_rows() -> list[dict[str, object]]:
    rows = []
    for projected in _projection_rows():
        local_end = projected["ts"].replace(tzinfo=SHANGHAI)
        rows.append(
            {
                "record_source": "base24_native5m_exact",
                "source_record_id": (
                    f"partition={projected['input_partition_id']}&stock_code=600000&ts="
                    f"{projected['ts'].isoformat()}"
                ),
                "source_revision": "base24_native5m_exact@" + "b" * 16,
                "source_artifact_sha256": HASH_B,
                "knowledge_from": datetime(2026, 10, 16, tzinfo=timezone.utc),
                "knowledge_to": None,
                "observation_start": (local_end - timedelta(minutes=5)).astimezone(timezone.utc),
                "observation_end": local_end.astimezone(timezone.utc),
                "listing_id": "listing-1",
                "calendar_session_id": "session-1",
                "interval_size": "5m",
                "price_basis": "UNADJUSTED",
                "currency": "CNY",
                "open": Decimal("1.0000000000"),
                "high": Decimal("1.0000000000"),
                "low": Decimal("1.0000000000"),
                "close": Decimal("1.0000000000"),
                "volume": Decimal("0E-10"),
                "turnover": Decimal("0E-10"),
                "trade_count": None,
            }
        )
    return rows


def _daily_row() -> dict[str, object]:
    return {
        "record_source": "base231_daily",
        "source_record_id": "daily-1",
        "source_revision": "base231_daily@test",
        "source_artifact_sha256": HASH_A,
        "knowledge_from": datetime(2026, 10, 15, tzinfo=timezone.utc),
        "knowledge_to": None,
        "observation_start": datetime(2020, 1, 2, 7, tzinfo=timezone.utc),
        "observation_end": None,
        "listing_id": "listing-1",
        "calendar_session_id": "session-1",
        "interval_size": "1d",
        "price_basis": "UNADJUSTED",
        "currency": "CNY",
        "open": Decimal("1.0000000000"),
        "high": Decimal("1.0000000000"),
        "low": Decimal("1.0000000000"),
        "close": Decimal("1.0000000000"),
        "volume": Decimal("0E-10"),
        "turnover": Decimal("0E-10"),
        "trade_count": None,
    }


def _final_month(root: Path) -> tuple[str, pa.Table]:
    table = pa.Table.from_pylist(
        [*_supplement_rows(), _daily_row()], schema=_schemas()["dwd_security_bar"]
    )
    relative = (
        "payload/dwd_security_bar/interval_size=mixed/observation_start_year=2020/"
        "observation_start_month=1/part-00000.parquet"
    )
    path = root / relative
    path.parent.mkdir(parents=True)
    pq.write_table(table, path, compression="zstd")
    return relative, table


def _resolution(
    projected: pa.Table,
    supplement: pa.Table,
    *,
    venue: str = "SSE",
    session_type: str = "REGULAR_DAY",
    duplicate_closed_calendar_version: bool = False,
) -> dict[str, int]:
    connection = duckdb.connect()
    connection.register("projected_native5m", projected)
    connection.register("native5m_supplement", supplement)
    connection.execute(
        f"CREATE TABLE dwd_listing AS SELECT 'listing-1' listing_id,"
        f"'600000' listing_code,'{venue}' venue_id"
    )
    connection.execute(
        "CREATE TABLE dwd_trading_venue AS SELECT ? venue_id,? mic_code",
        [venue, "XSHG" if venue == "SSE" else "XSHE"],
    )
    connection.execute(
        f"""
        CREATE TABLE dwd_trading_calendar AS SELECT 'session-1' calendar_session_id,
          'SSE' venue_id,DATE '2020-01-02' trade_date,'{session_type}' session_type,true is_open,
          TIMESTAMPTZ '2026-10-09T13:00:00+08:00' knowledge_from,NULL::TIMESTAMPTZ knowledge_to
        """
    )
    if duplicate_closed_calendar_version:
        connection.execute(
            "INSERT INTO dwd_trading_calendar SELECT 'session-1','SSE',"
            f"DATE '2020-01-02','{session_type}',true,"
            "TIMESTAMPTZ '2026-10-09T12:00:00+08:00',"
            "TIMESTAMPTZ '2026-10-09T13:00:00+08:00'"
        )
    connection.execute(
        """
        CREATE TABLE required_native5m AS SELECT '600000' stock_code,
          'SSE' venue,DATE '2020-01-02' trade_date
        """
    )
    try:
        return audit_month_resolution(
            connection, business_month="2020-01", expected_rows=48
        )
    finally:
        connection.close()


def _identity() -> dict[str, object]:
    return freeze_month_identity(
        business_month="2020-01",
        projected_security_days=1,
        ledger={"manifest_sha256": HASH_A},
        acquisition={"manifest_sha256": HASH_A},
        denominator={"plan_sha256": HASH_A},
        exact_members=[{"path": "/input/a.parquet", "sha256": HASH_B}],
        predecessor_members=[{"path": "/input/old.parquet", "sha256": HASH_A}],
        reference_members=[{"path": "/input/listing.parquet", "sha256": HASH_A}],
        code={"revision": "test", "module_sha256": HASH_A},
    )


def test_successor_ledger_is_exact_pass_gate_and_month_source_selection(tmp_path):
    root = _ledger(tmp_path / "ledger")
    validated = validate_successor_ledger(root, file_sha(root / "manifest.json"))
    assert validated["projected_rows"] == 48
    assert validated["months"] == [
        {
            "month": "2020-01",
            "projected_security_days": 1,
            "projected_rows": 48,
            "denominator_security_days": 1,
        }
    ]
    selected = select_month_source_members(
        [
            {
                "source_path": "/sealed/a.parquet",
                "partition_id": "a" * 64,
                "source_artifact_sha256": HASH_A,
                "acquired_at": "2026-10-16T00:00:00+00:00",
                "required_dates": ["2020-01-02", "2020-02-03"],
            }
        ],
        "2020-01",
    )
    assert selected[0]["required_dates"] == ["2020-01-02"]

    bad = _ledger(tmp_path / "bad-ledger", disposition="UNRESOLVED")
    with pytest.raises(Base24DownstreamError, match="BASE24_SUCCESSOR_LEDGER_CENSUS_INVALID"):
        validate_successor_ledger(bad, file_sha(bad / "manifest.json"))


@pytest.mark.parametrize(
    "source_family",
    ["tushare_partition_gated_native_5m", "tushare_bounded_continuous_session_native_5m"],
)
def test_successor_ledger_projects_each_qualified_native5m_family(tmp_path, source_family):
    root = _ledger(tmp_path / source_family, source_family=source_family)
    validated = validate_successor_ledger(root, file_sha(root / "manifest.json"))
    assert validated["projected_security_days"] == 1
    assert validated["projected_rows"] == 48


def test_month_transfer_requires_exact_resolution_before_tmp_release_and_global_ready(tmp_path):
    output = tmp_path / "dwd"
    output.mkdir()
    relative, final_table = _final_month(output)
    month_root = tmp_path / "months/2020-01"
    projection_root, projected = _projection(month_root / "tmp/projection")
    supplement = final_table.filter(pa.compute.equal(final_table["interval_size"], "5m"))
    resolution = _resolution(projected, supplement)

    receipt = seal_month_transfer(
        month_root=month_root,
        final_root=output,
        month_identity=_identity(),
        projection_root=projection_root,
        final_files=[relative],
        resolution=resolution,
    )
    assert receipt["ownership"] == "FINAL_DWD"
    assert (month_root / "TRANSFERRED").is_file()
    retirement = retire_projection_tmp(
        month_root=month_root, projection_root=projection_root, final_root=output
    )
    assert retirement["projection_tmp_absent"] is True
    assert not projection_root.exists()

    manifest = seal_global_transfer(
        output_root=output,
        month_roots=[month_root],
        expected_months=["2020-01"],
        registration={"contract": "test-global-registration"},
        code_revision="test",
    )
    assert manifest["new_native5m_rows"] == 48
    assert (output / "READY").is_file()
    audit = audit_global_transfer(output)
    assert audit["status"] == "PASS"
    assert audit["rows"] == 49
    assert audit["version_keys"] == 49


def test_wrong_venue_records_bounded_failure_and_never_writes_ready(tmp_path):
    month_root = tmp_path / "months/2020-01"
    month_root.mkdir(parents=True)
    projected = pa.Table.from_pylist(_projection_rows())
    supplement = pa.Table.from_pylist(
        _supplement_rows(), schema=_schemas()["dwd_security_bar"]
    )
    with pytest.raises(Base24DownstreamError, match="BASE24_MONTHLY_RESOLUTION_INVALID"):
        _resolution(projected, supplement, venue="SZSE")
    failure = record_month_failure(
        month_root=month_root,
        business_month="2020-01",
        stage="resolve_dwd",
        reason="BASE24_MONTHLY_RESOLUTION_INVALID",
        month_identity=_identity()["identity"],
    )
    assert failure["evidence_retained"] is True
    assert not (month_root / "TRANSFERRED").exists()
    assert not (tmp_path / "dwd/READY").exists()
    persisted = json.loads((month_root / "FAILURE.safe.json").read_text())
    assert set(persisted) == {
        "business_month",
        "contract",
        "evidence_retained",
        "final_dwd_bytes_retained",
        "identity",
        "month_identity",
        "projection_tmp_retained",
        "ready_written",
        "reason",
        "stage",
        "status",
        "transfer_marker_written",
    }


def test_month_resolution_accepts_governed_daily_session_alias():
    projected = pa.Table.from_pylist(_projection_rows())
    supplement = pa.Table.from_pylist(
        _supplement_rows(), schema=_schemas()["dwd_security_bar"]
    )
    resolution = _resolution(projected, supplement, session_type="day")
    assert resolution["resolved_rows"] == 48


def test_month_resolution_uses_one_active_calendar_knowledge_version():
    projected = pa.Table.from_pylist(_projection_rows())
    supplement = pa.Table.from_pylist(
        _supplement_rows(), schema=_schemas()["dwd_security_bar"]
    )
    resolution = _resolution(
        projected,
        supplement,
        session_type="day",
        duplicate_closed_calendar_version=True,
    )
    assert resolution["venue_calendar_rows"] == 48
