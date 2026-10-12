"""Synthetic joint delivery, native provenance, PIT, exact mirroring and failure mail."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines import p10_complete_daily as full
from quanthus.pipelines import p10_complete_daily_cycle as cycle
from quanthus.pipelines import p10_complete_daily_deadline as deadline
from quanthus.pipelines import p10_complete_daily_notifications as notifications
from quanthus.pipelines import p10_complete_daily_schedule as schedule
from quanthus.pipelines import p10_complete_daily_sync as mirror
from quanthus.pipelines import p10_native_minute_delivery as minute
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, file_digest
from quanthus.pipelines.p10_minute_operation_runtime import run_one
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
    _daily_core_quality_sha256,
)
from quanthus.platform.complete_daily_source import CompleteDailySource
from quanthus.providers import p10_native_m5_shard_capture as native_capture

DAY = "2026-10-12"
SHA = "sha256:" + "a" * 64
REVISION = "c" * 40
ACQUIRED = datetime(2026, 10, 13, 2, 40, tzinfo=timezone.utc)


def calendar() -> list[dict]:
    first = date(2026, 9, 25)
    closed = {date(2026, 9, 28) + timedelta(days=i) for i in range(10)}
    return [
        {
            "trade_date": (first + timedelta(days=i)).isoformat(),
            "is_open": str(
                int(
                    (first + timedelta(days=i)).weekday() < 5
                    and first + timedelta(days=i) not in closed
                )
            ),
        }
        for i in range(25)
    ]


def synthetic_reference(root: Path) -> Path:
    securities = {
        f"{i:06}": {"close": "10.5", "volume": "0", "tradestatus": "0"} for i in range(1, 4993)
    }
    securities["000001"] = {"close": "10.5", "volume": "20", "tradestatus": "1"}
    reference = {
        "kind": "p10_complete_daily_job_reference_v1",
        "market_date": DAY,
        "policy_id": schedule.POLICY_ID,
        "population": 4992,
        "source_ods_manifest_sha256": SHA,
        "event_ods_manifest_sha256": "sha256:" + "b" * 64,
        "securities": securities,
    }
    reference["reference_digest"] = digest(encoded(reference))
    path = root / "reference.json"
    atomic(path, reference)
    return path


def raw_m1() -> bytes:
    return encoded(
        {
            "code": 0,
            "data": {
                "sz000001": {
                    "m1": [
                        ["202610120930", "10.5", "10.5", "10.5", "10.5", "0", {}],
                        ["202610121500", "10.5", "10.5", "10.5", "10.5", ".2", {}],
                    ]
                }
            },
        }
    )


def raw_m5(*, shares: str = "20") -> bytes:
    return encoded(
        {
            "provider": "baostock-native-5m",
            "code": "sz.000001",
            "market_date": DAY,
            "frequency": "5",
            "adjustflag": "3",
            "fields": minute.FIELDS.split(","),
            "rows": [
                [
                    DAY,
                    "20261012150000000",
                    "sz.000001",
                    "10.5",
                    "10.5",
                    "10.5",
                    "10.5",
                    shares,
                    "210.00",
                ]
            ],
        }
    )


def capture(root: Path, interval: str, raw: bytes) -> Path:
    shard = root / interval / "shard-000"
    (shard / "raw").mkdir(parents=True)
    (shard / "receipts").mkdir()
    atomic(
        shard / "identity.json",
        {
            "kind": "p10_tencent_native_m1_raw_capture_shard"
            if interval == "1m"
            else "p10_baostock_native_m5_raw_capture_shard",
            "market_date": DAY,
            "market_ods_manifest_sha256": SHA,
            "selected_symbols": ["000001"],
        },
    )
    (shard / "raw/000001.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    atomic(
        shard / "receipts/000001.json",
        {
            "stock_code": "000001",
            "market_date": DAY,
            "status": "raw_saved",
            "source_market_ods_sha256": SHA,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "captured_at": ACQUIRED.isoformat(),
        },
    )
    return root / interval


def synthetic_daily(root: Path) -> Path:
    root.mkdir()
    timestamp = pa.timestamp("us", tz="UTC")
    schema = pa.schema(
        [
            (key, pa.string())
            for key in (
                "record_source",
                "source_record_id",
                "source_revision",
                "source_artifact_sha256",
            )
        ]
        + [
            (key, timestamp)
            for key in ("knowledge_from", "knowledge_to", "observation_start", "observation_end")
        ]
        + [
            (key, pa.string())
            for key in (
                "listing_id",
                "calendar_session_id",
                "interval_size",
                "price_basis",
                "currency",
            )
        ]
        + [
            (key, pa.decimal128(38, 10))
            for key in ("open", "high", "low", "close", "volume", "turnover")
        ]
        + [("trade_count", pa.int64())]
    )
    rows = []
    start = datetime(2026, 10, 11, 16, tzinfo=timezone.utc)
    for i in range(1, 4993):
        code = f"{i:06}"
        rows.append(
            {
                "record_source": "synthetic-daily",
                "source_record_id": f"daily.parquet#code={code}&date={DAY}",
                "source_revision": "synthetic-v1",
                "source_artifact_sha256": SHA,
                "knowledge_from": ACQUIRED - timedelta(hours=12),
                "knowledge_to": None,
                "observation_start": start,
                "observation_end": start + timedelta(days=1),
                "listing_id": "listing-" + code,
                "calendar_session_id": "calendar-sz",
                "interval_size": "1d",
                "price_basis": "none",
                "currency": "CNY",
                **{key: Decimal("10.5") for key in ("open", "high", "low", "close")},
                "volume": Decimal(20 if i == 1 else 0),
                "turnover": Decimal(210 if i == 1 else 0),
                "trade_count": None,
            }
        )
    counts = {}
    from quanthus.pipelines.securities_warehouse.daily_core_domain import _TABLE_KEYS

    for table in _TABLE_KEYS:
        path = root / f"payload/synthetic/{table}/market_date={DAY}/part-00000.parquet"
        path.parent.mkdir(parents=True)
        data = (
            pa.Table.from_pylist(rows, schema=schema)
            if table == "dwd_security_bar"
            else pa.table({"synthetic": ["row"]})
        )
        pq.write_table(data, path)
        counts[table] = data.num_rows
    atomic(
        root / "audit.json", {"result": "accepted", "market_date": DAY, "mode": "synthetic_only"}
    )
    files = [
        {
            "path": p.relative_to(root).as_posix(),
            "sha256": file_digest(p),
            "size_bytes": p.stat().st_size,
        }
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]
    from quanthus.pipelines.securities_warehouse.daily_core_domain import _canonical_bytes

    manifest = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_increment",
        "package_id": "synthetic-daily",
        "market_date": DAY,
        "covered_after_date": "2026-10-09",
        "code_revision": REVISION,
        "product_scope": "daily-core-v1",
        "quality_policy": "p10-business-value-warehouse-quality.v1",
        "quality_policy_sha256": _daily_core_quality_sha256(),
        "consumable_intervals": ["1d"],
        "selected_releases": {
            "market_daily_core": {"manifest_sha256": SHA},
            "calendar_daily_events": {"manifest_sha256": "sha256:" + "b" * 64},
        },
        "unavailable_products": {
            "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
            "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
        },
        "base": {
            "package_id": "synthetic-base-binding",
            "manifest_sha256": full.BASE_SHA,
            "content_digest": full.BASE_CONTENT,
        },
        "row_counts": counts,
        "files": files,
        "content_digest": digest(_canonical_bytes(files)),
    }
    atomic(root / "manifest.json", manifest)
    (root / "READY").write_text(file_digest(root / "manifest.json") + "\n")
    return root


@pytest.fixture
def members(tmp_path: Path) -> tuple[Path, Path, Path]:
    ref = synthetic_reference(tmp_path)
    paths = []
    for interval, raw in [("1m", raw_m1()), ("5m", raw_m5())]:
        source = capture(tmp_path / "capture", interval, raw)
        result = minute.build(
            reference_file=ref,
            capture_root=source,
            interval=interval,
            product_root=tmp_path / "minute" / interval,
            code_revision=REVISION,
        )
        paths.append(Path(result["package_path"]))
    return synthetic_daily(tmp_path / "daily"), paths[0], paths[1]


def test_tplus1_skips_weekend_and_holiday_and_missing_calendar_blocks() -> None:
    rows = calendar()
    assert schedule.target_for(datetime(2026, 10, 12, 10, 30, tzinfo=schedule.CST), rows) == date(
        2026, 10, 9
    )
    assert schedule.target_for(datetime(2026, 10, 10, 10, 30, tzinfo=schedule.CST), rows) is None
    assert schedule.checkpoint(date(2026, 10, 9), "minute_start", rows).date() == date(2026, 10, 12)
    assert schedule.checkpoint(date(2026, 9, 25), "minute_start", rows).date() == date(2026, 10, 8)
    with pytest.raises(ValueError, match="absent"):
        schedule.target_for(
            datetime(2026, 10, 12, 10, 30, tzinfo=schedule.CST),
            [row for row in rows if row["trade_date"] != "2026-10-11"],
        )


def test_v3_atomic_runner_authorizes_next_trading_day_not_saturday(tmp_path: Path) -> None:
    atomic(tmp_path / "calendar.json", calendar())
    args = {
        "state_root": tmp_path / "ledger",
        "calendar_file": tmp_path / "calendar.json",
        "market_date": date(2026, 10, 9),
        "phase": "T_plus_1",
        "operation_id": "test",
        "argv": ["/usr/bin/true"],
        "timeout_seconds": 5,
        "policy_id": schedule.POLICY_ID,
    }
    with pytest.raises(ValueError, match="outside"):
        run_one(**args, now=datetime(2026, 10, 10, 10, 30, tzinfo=schedule.CST))
    assert (
        run_one(**args, now=datetime(2026, 10, 12, 10, 30, tzinfo=schedule.CST))["status"]
        == "success"
    )


def test_native5m_is_observed_decimal_and_gap_aware_not_derived() -> None:
    quality, bars = minute.assess(
        interval="5m",
        code="000001",
        day=DAY,
        raw=raw_m5(),
        daily={"volume": "20", "close": "10.5", "tradestatus": "1"},
    )
    assert quality["quality_grade"] == "R2"
    assert len(bars) == 1 and isinstance(next(iter(bars.values()))["close"], Decimal)
    assert len(quality["missing_actual_minutes"]) == 47
    doc = json.loads(raw_m5())
    doc["adjustflag"] = "2"
    with pytest.raises(ValueError, match="provenance"):
        minute.native_m5(encoded(doc), "000001", DAY)


def test_m5_R3_is_not_formal_and_blocks_joint_delivery(tmp_path: Path) -> None:
    ref = synthetic_reference(tmp_path)
    source = capture(tmp_path / "capture", "5m", raw_m5(shares="21"))
    result = minute.build(
        reference_file=ref,
        capture_root=source,
        interval="5m",
        product_root=tmp_path / "minute",
        code_revision=REVISION,
    )
    assert result["quality_grades"]["R3"] == 1 and result["research_ready"] is False
    assert not (Path(result["package_path"]) / "ods/raw/000001.json.gz").exists()


def test_complete_projection_keeps_Decimal_time_keys_auction_and_R2(
    members: tuple, tmp_path: Path
) -> None:
    daily, m1, m5 = members
    result = full.build(
        daily=daily, m1=m1, m5=m5, output_root=tmp_path / "complete", code_revision=REVISION
    )
    pointer = json.loads((tmp_path / "complete" / full.POINTER).read_text())
    package = (tmp_path / "complete" / pointer["manifest_path"]).parent
    assert full.verify(package)["minute_row_counts"] == {"1m": 2, "5m": 1}
    rows = pq.ParquetFile(package / full.MINUTE_FILE).read().to_pylist()
    assert all(row["knowledge_from"] == ACQUIRED for row in rows)
    assert all(row["quality_grade"] == "R2" for row in rows)
    assert rows[0]["observation_start"] == rows[0]["observation_end"]
    assert rows[1]["observation_end"] - rows[1]["observation_start"] == timedelta(minutes=1)
    assert rows[2]["observation_end"] - rows[2]["observation_start"] == timedelta(minutes=5)
    assert rows[1]["turnover"] is None and rows[2]["turnover"] == Decimal("210")
    assert (
        full.build(
            daily=daily, m1=m1, m5=m5, output_root=tmp_path / "complete", code_revision=REVISION
        )["manifest_sha256"]
        == result["manifest_sha256"]
    )


def test_forged_grade_or_native_projection_fails_even_if_resealed(
    members: tuple, tmp_path: Path
) -> None:
    _, m1, _ = members
    path = m1 / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["grades"]["R0"] = manifest["grades"].pop("R2")
    atomic(path, manifest)
    (m1 / "READY").write_text(file_digest(path) + "\n")
    with pytest.raises(ValueError, match="census"):
        minute.verify(m1)


def test_waits_for_sealed_daily_before_any_supplier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cycle, "failure", lambda **_: {"status": "synthetic_no_email"})
    monkeypatch.setattr(
        cycle, "find_market_ods", lambda *_: (_ for _ in ()).throw(ValueError("not ready"))
    )
    monkeypatch.setattr(
        cycle, "run_interval", lambda **_: pytest.fail("supplier started without daily ODS")
    )
    result = cycle.run(
        now=datetime(2026, 10, 13, 10, 30, tzinfo=schedule.CST),
        rows=calendar(),
        revision=REVISION,
        journal=tmp_path / "journal",
        releases=tmp_path / "releases",
    )
    assert result["status"] == "waiting_for_sealed_daily_ods"


def test_failure_notification_is_once_per_stage_and_retries_failed_mail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    def send(**kwargs: object) -> dict:
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("transport failure")
        return {"email_id": "synthetic"}

    monkeypatch.setattr(notifications.mail, "_send_resend", send)
    args = {
        "day": date.fromisoformat(DAY),
        "stage": "minutes",
        "state_root": tmp_path,
        "reason": "R3",
    }
    with pytest.raises(RuntimeError):
        notifications.failure(**args)
    notifications.failure(**args)
    assert (
        notifications.failure(**{**args, "reason": "still R3 but new details"})["status"]
        == "already_sent"
    )
    assert len(calls) == 2 and calls[0]["key"] == calls[1]["key"]


def test_exact_mirror_and_consumer_read_actual_bars_without_execution(
    members: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily, m1, m5 = members
    source = tmp_path / "source"
    full.build(daily=daily, m1=m1, m5=m5, output_root=source, code_revision=REVISION)
    monkeypatch.setenv("QUANTHUS_CN01_DWD_REMOTE_ROOT", mirror.REMOTE)
    monkeypatch.setattr(mirror, "_ssh_command", lambda: ("cn01", "22", "reader", "", "", "ssh"))

    def transfer(*, source: str, destination: Path, ssh: str, extra: tuple = ()) -> None:
        local = tmp_path / "source" / source.split(mirror.REMOTE + "/", 1)[1]
        if local.is_dir():
            shutil.copytree(local, destination, dirs_exist_ok=True)
        else:
            shutil.copyfile(local, destination)

    monkeypatch.setattr(mirror, "_run_rsync", transfer)
    target = tmp_path / "oci"
    assert mirror.sync(root=target, target=date.fromisoformat(DAY))["status"] == "synced"
    assert mirror.sync(root=target, target=date.fromisoformat(DAY))["status"] == "already_verified"
    consumer = CompleteDailySource(target)
    admitted = consumer.admission(("1m", "5m"))
    cutoff = datetime.fromisoformat(admitted["first_verified_visible_at"]) + timedelta(seconds=1)
    # The synthetic acquisition time may be in the future relative to this test's clock.
    cutoff = max(cutoff, ACQUIRED + timedelta(seconds=1))
    rows = list(consumer.iter_bars(required_intervals=("1m", "5m"), knowledge_cutoff=cutoff))
    assert len(rows) == 2 and {row["interval_size"] for row in rows} == {"1m", "5m"}
    assert admitted["execution_authorized"] is False
    with pytest.raises(ValueError, match="visible"):
        list(
            consumer.iter_bars(
                required_intervals=("5m",),
                knowledge_cutoff=datetime(2026, 9, 30, tzinfo=timezone.utc),
            )
        )
    with pytest.raises(ValueError, match="execution"):
        CompleteDailySource(target, purpose="trading_execution")


def test_mirror_recovers_exact_missed_revision_and_keeps_first_visibility(
    members: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily, m1, m5 = members
    source = tmp_path / "source"
    first = full.build(daily=daily, m1=m1, m5=m5, output_root=source, code_revision=REVISION)
    second = full.build(daily=daily, m1=m1, m5=m5, output_root=source, code_revision="d" * 40)
    assert first["manifest_sha256"] != second["manifest_sha256"]
    monkeypatch.setenv("QUANTHUS_CN01_DWD_REMOTE_ROOT", mirror.REMOTE)
    monkeypatch.setattr(mirror, "_ssh_command", lambda: ("cn01", "22", "reader", "", "", "ssh"))

    def transfer(*, source: str, destination: Path, ssh: str, extra: tuple = ()) -> None:
        local = tmp_path / "source" / source.split(mirror.REMOTE + "/", 1)[1]
        if local.is_dir():
            shutil.copytree(local, destination, dirs_exist_ok=True)
        else:
            shutil.copyfile(local, destination)

    monkeypatch.setattr(mirror, "_run_rsync", transfer)
    target = tmp_path / "oci"
    result = mirror.sync(root=target, target=date.fromisoformat(DAY))
    assert result["manifest_sha256"] == second["manifest_sha256"]
    assert len(list((target / "visibility").glob("*.json"))) == 2
    assert len(list((target / "packages" / DAY).glob("*/manifest.json"))) == 2
    assert (
        mirror.sync(root=target, target=date.fromisoformat(DAY))["first_verified_visible_at"]
        == result["first_verified_visible_at"]
    )


def test_independent_oci_deadline_rejects_missing_and_late_visibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = {"stage": "mirror", "rows": calendar(), "mirror_root": tmp_path}
    assert (
        deadline.evaluate(**args, now=datetime(2026, 10, 13, 13, 59, tzinfo=schedule.CST))["status"]
        == "not_yet_deadline"
    )
    assert (
        deadline.evaluate(**args, now=datetime(2026, 10, 13, 14, 0, tzinfo=schedule.CST))["status"]
        == "failed"
    )
    monkeypatch.setattr(
        CompleteDailySource,
        "admission",
        lambda *_: {"market_date": DAY, "first_verified_visible_at": "2026-10-13T06:00:01+00:00"},
    )
    assert (
        deadline.evaluate(**args, now=datetime(2026, 10, 13, 14, 1, tzinfo=schedule.CST))["status"]
        == "failed"
    )


def test_readonly_export_permissions_cover_members_and_atomic_pointers(
    members: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import grp
    import os

    monkeypatch.setenv("QUANTHUS_COMPLETE_DAILY_EXPORT_GROUP", grp.getgrgid(os.getgid()).gr_name)
    daily, m1, m5 = members
    root = tmp_path / "complete"
    full.build(daily=daily, m1=m1, m5=m5, output_root=root, code_revision=REVISION)
    pointer = json.loads((root / full.POINTER).read_text())
    package = (root / pointer["manifest_path"]).parent
    for path in [
        root / full.POINTER,
        package,
        package / full.MINUTE_FILE,
        package / "members/1m/READY",
    ]:
        assert path.stat().st_gid == os.getgid()
        assert path.stat().st_mode & 0o777 == (0o750 if path.is_dir() else 0o640)


def test_native5m_capture_uses_observed_sdk_strings_and_immutable_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    class Response:
        error_code = "0"

        def __init__(self) -> None:
            self.remaining = True

        def next(self) -> bool:
            result, self.remaining = self.remaining, False
            return result

        def get_row_data(self) -> list[str]:
            return json.loads(raw_m5())["rows"][0]

    class Client:
        def __init__(self) -> None:
            self.queries = []

        def login(self) -> object:
            return SimpleNamespace(error_code="0")

        def logout(self) -> None:
            pass

        def query_history_k_data_plus(self, *args: object, **kwargs: object) -> Response:
            self.queries.append((args, kwargs))
            return Response()

    client = Client()
    monkeypatch.setattr(native_capture, "set_socket_timeout", lambda *_: True)
    monkeypatch.setattr(native_capture.time, "sleep", lambda *_: None)
    args = {
        "codes": ["000001"],
        "market_date": date.fromisoformat(DAY),
        "market_manifest_sha": SHA,
        "output": tmp_path,
        "shard_index": 0,
        "client": client,
    }
    assert native_capture.capture_shard(**args)["saved"] == 1
    assert native_capture.capture_shard(**args)["saved"] == 1
    assert len(client.queries) == 1
    kwargs = client.queries[0][1]
    assert kwargs["frequency"] == "5" and kwargs["adjustflag"] == "3"
    raw = gzip.decompress((tmp_path / "raw/000001.json.gz").read_bytes())
    assert json.loads(raw)["rows"][0][6] == "10.5"
    assert minute.native_m5(raw, "000001", DAY)["202610121500"]["close"] == Decimal("10.5")
