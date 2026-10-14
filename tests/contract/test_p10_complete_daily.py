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


@pytest.mark.parametrize("interval", ["1m", "5m"])
def test_versioned_suspended_reference_keeps_unavailable_prices_null(
    tmp_path: Path, interval: str
) -> None:
    path = tmp_path / "reference.json"
    securities = {
        "000001": {"tradestatus": "1", "close": "10.5", "volume": "20"},
        "000002": {
            "tradestatus": "0",
            "close": None,
            "volume": None,
            "daily_price_observation_state": "unavailable_suspended",
        },
    }
    value = {
        "kind": "p10_complete_daily_job_reference_v3",
        "market_date": DAY,
        "policy_id": schedule.POLICY_ID,
        "population": 2,
        "securities": securities,
        "universe_policy_id": "p10-acquisition-prevention-v1",
        "universe_digest": digest(encoded(sorted(securities))),
        "universe_manifest_sha256": SHA,
    }
    atomic(path, {**value, "reference_digest": digest(encoded(value))})
    ref = minute.reference(path)
    status, rows = minute.assess(
        interval=interval, code="000002", day=DAY, raw=None, daily=ref["securities"]["000002"]
    )
    assert status["quality_grade"] == "SUSPENDED" and rows == {}
    assert status["daily_close_delta"] is None and status["daily_share_delta"] is None
    assert status["formally_ingested"] is False
    value["securities"]["000002"]["tradestatus"] = "1"
    atomic(path, {**value, "reference_digest": digest(encoded(value))})
    with pytest.raises(ValueError, match="unavailable suspended price"):
        minute.reference(path)


def test_daily_mapping_uses_suspension_identity_without_fabricating_a_bar(tmp_path: Path) -> None:
    root = synthetic_daily(tmp_path / "daily")
    bar_path = next(root.glob("payload/*/dwd_security_bar/market_date=*/part-*.parquet"))
    bar = pq.ParquetFile(bar_path).read()
    original = bar.to_pylist()
    suspended = original[1]
    pq.write_table(pa.Table.from_pylist([original[0], *original[2:]], schema=bar.schema), bar_path)
    state_path = next(root.glob("payload/*/dwd_tradability_state/market_date=*/part-*.parquet"))
    state = {
        key: suspended[key]
        for key in ("listing_id", "calendar_session_id", "source_record_id", "knowledge_from")
    }
    state.update(is_tradable=False, can_buy=False, can_sell=False)
    pq.write_table(pa.Table.from_pylist([state]), state_path)
    manifest = json.loads((root / "manifest.json").read_text())
    codes = [f"{i:06}" for i in range(1, 4993)]
    manifest["acquisition_universe"] = {
        "market_date": DAY,
        "policy_id": "p10-acquisition-prevention-v1",
        "population": 4992,
        "codes": codes,
        "universe_digest": digest(encoded(codes)),
    }
    atomic(root / "manifest.json", manifest)
    mapping, schema = full.daily_mapping(root, DAY)
    assert len(mapping) == 4992 and schema == bar.schema
    assert mapping["000002"]["listing_id"] == suspended["listing_id"]
    assert mapping["000002"]["daily_price_observation_state"] == "unavailable_suspended"
    assert "close" not in mapping["000002"] and "volume" not in mapping["000002"]
    state["is_tradable"] = True
    pq.write_table(pa.Table.from_pylist([state]), state_path)
    with pytest.raises(ValueError, match="lacks exact suspended"):
        full.daily_mapping(root, DAY)


@pytest.mark.parametrize("interval,raw", [("1m", raw_m1()), ("5m", raw_m5())])
def test_suspension_cannot_hide_positive_target_minute_volume(interval: str, raw: bytes) -> None:
    daily = {
        "tradestatus": "0",
        "close": None,
        "volume": None,
        "daily_price_observation_state": "unavailable_suspended",
    }
    quality, bars = minute.assess(interval=interval, code="000001", day=DAY, raw=raw, daily=daily)
    assert quality["quality_grade"] == "R3" and bars == {}
    assert quality["quality_reason"] == "suspended_with_positive_target_minute_volume"
    assert quality["formally_ingested"] is False
    if interval == "1m":
        other = raw.replace(b"20261012", b"20261009")
    else:
        body = json.loads(raw)
        body["rows"] = []
        other = encoded(body)
    quality, bars = minute.assess(interval=interval, code="000001", day=DAY, raw=other, daily=daily)
    assert quality["quality_grade"] == "SUSPENDED" and bars == {}


def test_sealed_member_reuse_preserves_bytes_and_handles_cross_device_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    source = tmp_path / "source"
    source.write_bytes(b"immutable source bytes")
    target = tmp_path / "linked"
    full.copy_sealed_file(str(source), str(target))
    assert source.stat().st_ino == target.stat().st_ino
    assert file_digest(source) == file_digest(target)

    def cross_device(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EXDEV, "different devices")

    monkeypatch.setattr(os, "link", cross_device)
    copied = tmp_path / "copied"
    full.copy_sealed_file(str(source), str(copied))
    assert copied.stat().st_ino != source.stat().st_ino
    assert file_digest(copied) == file_digest(source)
    link = tmp_path / "unsafe"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular non-symlink"):
        full.copy_sealed_file(str(link), str(tmp_path / "rejected"))


@pytest.mark.parametrize("interval,index", [("1m", 1), ("5m", 2)])
def test_projection_hashes_immutable_manifest_once_per_member(
    members: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, interval: str, index: int
) -> None:
    member = members[index]
    mapping, _ = full.daily_mapping(members[0], DAY)
    manifest = member / "manifest.json"
    expected_revision = (
        ("tencent-mkline-m1" if interval == "1m" else "baostock-native-5m")
        + "@"
        + file_digest(manifest)[7:19]
    )
    original = full.file_digest
    calls = []

    def counted(path: Path) -> str:
        if path == manifest:
            calls.append(path)
        return original(path)

    monkeypatch.setattr(full, "file_digest", counted)
    rows = list(full.projected_rows(member, interval, mapping))
    assert len(rows) == (2 if interval == "1m" else 1)
    assert len(calls) == 1
    assert {row["source_revision"] for row in rows} == {expected_revision}
    assert all(isinstance(row["close"], Decimal) for row in rows)
    assert all(row["knowledge_from"].tzinfo is not None for row in rows)


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
        capture=tmp_path / "capture",
        product=tmp_path / "product",
        complete=tmp_path / "complete",
        daily=tmp_path / "daily",
        releases=tmp_path / "releases",
        notifications=tmp_path / "notifications",
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


def test_mirror_interruption_retains_private_partial_and_resumes_same_cache(
    members: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily, m1, m5 = members
    source_root = tmp_path / "source"
    full.build(daily=daily, m1=m1, m5=m5, output_root=source_root, code_revision=REVISION)
    monkeypatch.setenv("QUANTHUS_CN01_DWD_REMOTE_ROOT", mirror.REMOTE)
    monkeypatch.setattr(mirror, "_ssh_command", lambda: ("cn01", "22", "reader", "", "", "ssh"))
    interrupted = [True]
    targets = []

    def transfer(*, source: str, destination: Path, ssh: str, extra: tuple = ()) -> None:
        local = source_root / source.split(mirror.REMOTE + "/", 1)[1]
        if local.is_dir():
            targets.append(destination)
            if interrupted[0]:
                victim = next(local.rglob("*.parquet"))
                partial = destination / victim.relative_to(local)
                partial.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(victim.read_bytes()[:10])
                interrupted[0] = False
                raise mirror.WarehouseSyncError("interrupted", code="transfer_failed")
            shutil.copytree(local, destination, dirs_exist_ok=True)
        else:
            shutil.copyfile(local, destination)

    monkeypatch.setattr(mirror, "_run_rsync", transfer)
    target = tmp_path / "oci"
    with pytest.raises(mirror.WarehouseSyncError):
        mirror.sync(root=target, target=date.fromisoformat(DAY))
    assert targets[0].exists()
    assert (target / mirror.CACHE / "pending.json").exists()
    assert not (target / full.POINTER).exists()
    assert not (target / "visibility").exists()
    result = mirror.sync(root=target, target=date.fromisoformat(DAY))
    assert result["status"] == "synced" and targets[0] == targets[1]
    assert not (target / mirror.CACHE / "pending.json").exists()


def test_mirror_reuses_only_exact_sealed_local_files(
    members: tuple, tmp_path: Path
) -> None:
    daily, m1, m5 = members
    root = tmp_path / "oci"
    full.build(daily=daily, m1=m1, m5=m5, output_root=root, code_revision=REVISION)
    pointer = json.loads((root / full.POINTER).read_text())
    old = (root / pointer["manifest_path"]).parent
    manifest = json.loads((old / "manifest.json").read_text())
    incoming = root / mirror.CACHE / "incoming"
    incoming.mkdir(parents=True)
    mirror.seed_verified_files(root, incoming, manifest)
    victim = next(item for item in manifest["files"] if item["path"].endswith(".parquet"))
    old_file, new_file = old / victim["path"], incoming / victim["path"]
    assert new_file.stat().st_ino == old_file.stat().st_ino
    assert file_digest(new_file) == victim["sha256"]
    assert not (incoming / "READY").exists()
    changed = root / mirror.CACHE / "changed"
    changed.mkdir()
    expected = {**manifest, "files": [{**victim, "sha256": "sha256:" + "0" * 64}]}
    mirror.seed_verified_files(root, changed, expected)
    assert not (changed / victim["path"]).exists()


def test_pending_mirror_job_continues_across_next_trading_date(tmp_path: Path) -> None:
    atomic(
        tmp_path / mirror.CACHE / "pending.json",
        {"kind": "oci_resumable_complete_sync_v1", "market_date": DAY},
    )
    assert mirror.pending_target(tmp_path, date(2026, 10, 13)) == date.fromisoformat(DAY)
    assert mirror.pending_target(tmp_path, None) == date.fromisoformat(DAY)


def test_future_pending_still_waits_for_authoritative_checkpoint(tmp_path: Path) -> None:
    atomic(
        tmp_path / mirror.CACHE / "pending.json",
        {"kind": "oci_resumable_complete_sync_v1", "market_date": "2026-10-13"},
    )
    target = mirror.pending_target(tmp_path, None)
    assert target == date(2026, 10, 13)
    rows = calendar()
    # This remains the same gate used by main before sync, including on closed days.
    assert datetime(2026, 10, 13, 23, tzinfo=schedule.CST) < schedule.checkpoint(
        target, "minute_start", rows
    )


def test_mirror_private_cache_reuse_requires_actual_incoming_hash(tmp_path: Path) -> None:
    root = tmp_path / "oci"
    old = root / mirror.CACHE / "packages" / DAY / "old"
    old.mkdir(parents=True)
    atomic(old / "manifest.json", {"files": []})
    (old / "data.parquet").write_bytes(b"exact sealed bytes")
    item = {"path": "data.parquet", "bytes": 18, "sha256": file_digest(old / "data.parquet")}
    incoming = root / mirror.CACHE / "packages" / DAY / "new"
    incoming.mkdir(parents=True)
    mirror.seed_verified_files(root, incoming, {"files": [item]})
    assert (incoming / item["path"]).stat().st_ino == (old / item["path"]).stat().st_ino
    bad = incoming.parent / "bad"
    bad.mkdir()
    mirror.seed_verified_files(root, bad, {"files": [{**item, "sha256": "sha256:" + "0" * 64}]})
    assert not (bad / item["path"]).exists()


def test_complete_delayed_partial_can_seed_only_after_exact_hash(tmp_path: Path) -> None:
    root = tmp_path / "oci"
    old = root / mirror.CACHE / "packages" / DAY / "old"
    partial = old / "payload" / ".rsync-partial" / "data.parquet"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"exact sealed bytes")
    atomic(old / "manifest.json", {"files": []})
    item = {"path": "payload/data.parquet", "bytes": 18, "sha256": file_digest(partial)}
    incoming = root / mirror.CACHE / "packages" / DAY / "new"
    incoming.mkdir(parents=True)
    mirror.seed_verified_files(root, incoming, {"files": [item]})
    assert (incoming / item["path"]).stat().st_ino == partial.stat().st_ino
    bad = incoming.parent / "bad"
    bad.mkdir()
    mirror.seed_verified_files(root, bad, {"files": [{**item, "sha256": "sha256:" + "0" * 64}]})
    assert not (bad / item["path"]).exists()


def test_closed_day_main_resumes_due_pending_but_waits_for_future(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    class ClosedDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 17, 12, tzinfo=schedule.CST)

    root = tmp_path / "oci"
    root.mkdir()
    calendar_file = tmp_path / "calendar.json"
    calendar_file.write_text(json.dumps(calendar()))
    monkeypatch.setattr(mirror, "datetime", ClosedDay)
    monkeypatch.setattr(
        sys, "argv", ["sync", "--local-root", str(root), "--calendar-file", str(calendar_file)]
    )
    calls = []
    monkeypatch.setattr(
        mirror, "sync", lambda **kwargs: calls.append(kwargs["target"]) or {"status": "synced"}
    )
    pending = root / mirror.CACHE / "pending.json"
    atomic(pending, {"kind": "oci_resumable_complete_sync_v1", "market_date": "2026-10-15"})
    assert mirror.main() == 0
    assert calls == [date(2026, 10, 15)]
    # Friday's T+1 is Monday; a Saturday attempt must not start it early.
    atomic(pending, {"kind": "oci_resumable_complete_sync_v1", "market_date": "2026-10-16"})
    assert mirror.main() == 0
    assert calls == [date(2026, 10, 15)]


@pytest.mark.parametrize("fresh", [True, False])
@pytest.mark.parametrize("mode", ["--mirror-service-failure", "--mirror-service-stop"])
def test_mirror_failure_mail_uses_only_current_service_pending_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh: bool, mode: str
) -> None:
    import sys

    now = datetime(2026, 10, 14, 1, tzinfo=schedule.CST)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    atomic(
        tmp_path / mirror.CACHE / "pending.json",
        {
            "kind": "oci_resumable_complete_sync_v1",
            "market_date": DAY,
            "started_at": (now - timedelta(seconds=10 if fresh else 60)).isoformat(),
        },
    )
    monkeypatch.setattr(mirror, "LOCAL", tmp_path)
    monkeypatch.setattr(notifications, "datetime", Clock)
    monkeypatch.setattr(notifications, "_service_started_at", lambda _: now.timestamp() - 20)
    monkeypatch.setenv("SERVICE_RESULT", "timeout")
    monkeypatch.setattr(sys, "argv", ["notifications", mode])
    records = []
    monkeypatch.setattr(notifications, "failure", lambda **kw: records.append(kw) or {})
    assert notifications.main() == 0
    assert records[0]["stage"] == "mirror"
    assert records[0]["day"] == (date.fromisoformat(DAY) if fresh else now.date())


def test_mirror_stop_hook_is_in_same_attempt_and_success_sends_no_mail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setenv("SERVICE_RESULT", "success")
    monkeypatch.setattr(sys, "argv", ["notifications", "--mirror-service-stop"])
    monkeypatch.setattr(
        notifications, "failure", lambda **_: pytest.fail("success sent failure mail")
    )
    assert notifications.main() == 0
    unit = (schedule.CONFIG.parents[2] / "ops/systemd/oci-complete-daily-sync.service").read_text()
    assert "OnFailure=" not in unit
    assert "ExecStopPost=-" in unit and "--mirror-service-stop" in unit


def test_mirror_stop_hook_without_service_result_is_not_a_failure_drill(monkeypatch):
    import sys

    monkeypatch.delenv("SERVICE_RESULT", raising=False)
    monkeypatch.setattr(sys, "argv", ["notifications", "--mirror-service-stop"])
    with pytest.raises(ValueError, match="SERVICE_RESULT"):
        notifications.main()



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


def test_canary_source_admission_stops_at_13_but_grade_can_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import Mock

    atomic(tmp_path / "calendar.json", calendar())
    clock = Mock()
    clock.now.return_value = datetime(2026, 10, 13, 13, 5, tzinfo=schedule.CST)
    monkeypatch.setattr(cycle, "datetime", clock)
    runner = Mock(return_value={"status": "synthetic_success"})
    monkeypatch.setattr(cycle, "run_one", runner)
    args = {
        "journal": tmp_path,
        "calendar": tmp_path / "calendar.json",
        "target": date.fromisoformat(DAY),
        "command": ["/usr/bin/true"],
    }
    for operation in ("repair-canary-1230", "initial-capture-000", "repair-capture-001"):
        assert cycle._operation(**args, operation=operation)["status"] == "cutoff_reached"
    assert not runner.called
    assert cycle._operation(**args, operation="initial-grade")["status"] == "synthetic_success"


def test_installed_policy_config_resolves_from_application_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import importlib.util

    app = tmp_path / "app"
    (app / "config/pipelines").mkdir(parents=True)
    (app / "pyproject.toml").write_text('[project]\nname="synthetic-app"\n')
    shutil.copyfile(schedule.CONFIG, app / "config/pipelines/p10-complete-daily.v1.json")
    installed = tmp_path / "site-packages/quanthus/pipelines/p10_complete_daily_schedule.py"
    installed.parent.mkdir(parents=True)
    shutil.copyfile(schedule.__file__, installed)
    monkeypatch.chdir(app)
    spec = importlib.util.spec_from_file_location("synthetic_installed_schedule", installed)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert app / "config/pipelines/p10-complete-daily.v1.json" == module.CONFIG
    assert module.policy()["minimum_minute_grade"] == "R2"


def test_minute_deadline_reports_late_seal_even_when_quality_passes(
    members: tuple, tmp_path: Path
) -> None:
    _, m1, m5 = members
    for interval, package in [("1m", m1), ("5m", m5)]:
        manifest = json.loads((package / "manifest.json").read_text())
        manifest["sealed_at"] = "2026-10-13T05:00:01+00:00"
        atomic(package / "manifest.json", manifest)
        (package / "READY").write_text(file_digest(package / "manifest.json") + "\n")
        atomic(
            tmp_path / "minute" / interval / "date-pointers" / f"{DAY}.json",
            {
                "package_path": str(package),
                "manifest_sha256": file_digest(package / "manifest.json"),
                "first_verified_published_at": "2026-10-13T05:00:02+00:00",
            },
        )
    result = deadline.evaluate(
        stage="minutes",
        now=datetime(2026, 10, 13, 13, 5, tzinfo=schedule.CST),
        rows=calendar(),
        product=tmp_path / "minute",
    )
    assert result["status"] == "failed"

    for interval, package in [("1m", m1), ("5m", m5)]:
        manifest = json.loads((package / "manifest.json").read_text())
        manifest["sealed_at"] = "2026-10-13T04:59:58+00:00"
        atomic(package / "manifest.json", manifest)
        (package / "READY").write_text(file_digest(package / "manifest.json") + "\n")
        atomic(
            tmp_path / "minute" / interval / "date-pointers" / f"{DAY}.json",
            {
                "package_path": str(package),
                "manifest_sha256": file_digest(package / "manifest.json"),
                "first_verified_published_at": "2026-10-13T04:59:59+00:00",
            },
        )
    assert (
        deadline.evaluate(
            stage="minutes",
            now=datetime(2026, 10, 13, 13, 5, tzinfo=schedule.CST),
            rows=calendar(),
            product=tmp_path / "minute",
        )["status"]
        == "passed"
    )


def test_daily_cycle_failure_mail_uses_actual_failed_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    today = datetime.now(schedule.CST).date()
    monkeypatch.setenv("MONITOR_UNIT", "quanthus-daily-core@cn01.service")
    monkeypatch.setenv("MONITOR_INVOCATION_ID", "a" * 32)
    atomic(
        tmp_path / "daily/operations/LATEST.json",
        {
            "started_at": datetime.now(schedule.CST).isoformat(),
            "through_date": str(today),
            "invocation_id": "a" * 32,
            "attempts": [{"market_date": "2026-10-09", "returncode": 2}],
        },
    )
    monkeypatch.setattr(cycle, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(cycle, "JOURNAL", tmp_path / "journal")
    monkeypatch.setattr(
        notifications,
        "_daily_service_started_at",
        lambda: datetime.now(schedule.CST).timestamp() - 10,
    )
    observed = []
    monkeypatch.setattr(notifications, "failure", lambda **kwargs: observed.append(kwargs) or {})
    monkeypatch.setattr(sys, "argv", ["notification", "--daily-cycle-failure"])
    assert notifications.main() == 0
    assert observed[0]["day"] == date(2026, 10, 9)
    assert observed[0]["context"]["market_date_label"] == "交易日"


def test_daily_failure_stale_or_malformed_context_uses_explicit_unresolved_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(schedule.CST)
    monkeypatch.setattr(notifications, "_daily_service_started_at", lambda: now.timestamp() - 10)
    path = tmp_path / "LATEST.json"
    atomic(
        path,
        {
            "started_at": (now - timedelta(hours=1)).isoformat(),
            "attempts": [{"market_date": "2026-10-09", "returncode": 2}],
        },
    )
    for mode in ("stale", "malformed", "missing"):
        if mode == "malformed":
            path.write_text("{bad json")
        if mode == "missing":
            path.unlink()
        day, label = notifications._daily_failure_target(now, path)
        assert day == now.date() and "运行日期" in label and "未确定" in label


def test_complete_mirror_consumer_carries_domain_versions_and_explicit_gaps(
    members: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.pipelines import p10_domain_continuation as domain
    from quanthus.pipelines.p10_delivery_common import BASE23_CONTENT, BASE23_MANIFEST, seal

    domain_root = tmp_path / "domains"
    package = domain_root / "disclosure/packages" / DAY / "synthetic"
    payload = package / "payload/dwd_disclosure/part-00000.parquet"
    payload.parent.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([{"disclosure_id": "synthetic", "knowledge_from": ACQUIRED}]), payload
    )
    seal(
        package,
        {
            "schema_version": 1,
            "kind": domain.KIND,
            "domain": "disclosure",
            "policy_id": domain.POLICY,
            "market_date": DAY,
            "code_revision": REVISION,
            "base": {"manifest_sha256": BASE23_MANIFEST, "content_digest": BASE23_CONTENT},
            "consumer_purpose": domain.PURPOSE,
            "execution_authorized": False,
            "audit": {"row_counts": {"dwd_disclosure": 1}},
        },
    )
    checked = domain.verify(package)
    atomic(
        domain_root / "disclosure/date-pointers" / (DAY + ".json"),
        {
            "manifest_path": (package / "manifest.json")
            .relative_to(domain_root / "disclosure")
            .as_posix(),
            "manifest_sha256": checked["manifest_sha256"],
        },
    )
    monkeypatch.setenv("QUANTHUS_DOMAIN_CONTINUATION_ROOT", str(domain_root))
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", domain.POLICY)
    source = tmp_path / "source"
    full.build(
        daily=members[0], m1=members[1], m5=members[2], output_root=source, code_revision=REVISION
    )
    pointer = json.loads((source / full.POINTER).read_text())
    manifest = json.loads((source / pointer["manifest_path"]).read_text())
    assert manifest["domain_manifest_sha256"]["disclosure-" + DAY] == checked["manifest_sha256"]
    assert manifest["domain_disposition"]["financial"] == "unavailable_no_qualified_weekly_source"
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
    mirror.sync(root=target, target=date.fromisoformat(DAY))
    consumer = CompleteDailySource(target)
    cutoff = max(
        datetime.fromisoformat(consumer.admission()["first_verified_visible_at"])
        + timedelta(seconds=1),
        ACQUIRED + timedelta(seconds=1),
    )
    rows = list(consumer.iter_domain_rows(table="dwd_disclosure", knowledge_cutoff=cutoff))
    assert [item["disclosure_id"] for item in rows] == ["synthetic"]
    assert not list(consumer.iter_domain_rows(table="dwd_financial_fact", knowledge_cutoff=cutoff))
    with pytest.raises(ValueError, match="visible"):
        list(
            consumer.iter_domain_rows(
                table="dwd_disclosure", knowledge_cutoff=ACQUIRED - timedelta(days=30)
            )
        )
