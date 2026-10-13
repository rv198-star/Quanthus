from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.providers.acquisition_evidence import POLICY, capture, suspended_status


def write_parquet(path: Path, rows: list[dict]) -> None:
    from quanthus.pipelines.lane_runner import _write_parquet

    _write_parquet(rows, path)


DAY = "2026-10-13"
STAMP = "2026-10-13T13:21:00+00:00"
FIELDS = (
    "date,code,open,high,low,close,volume,amount,turn,pctChg,"
    "tradestatus,isST,peTTM,pbMRQ,psTTM,pcfNcfTTM"
)
ROW = [
    DAY,
    "sz.000001",
    "1.0000",
    "1.0000",
    "1.0000",
    "1.0000",
    "",
    "",
    "",
    "",
    "0",
    "1",
    "",
    "",
    "",
    "",
]


def test_proven_suspension_retains_status_without_inventing_bar() -> None:
    result = suspended_status(
        ROW, code="000001", provider_code="sz.000001", day=DAY, observed_at=STAMP
    )
    assert result is not None and result["tradestatus"] == "0" and result["isST"] == "1"
    assert result["acquired_at"] == STAMP
    assert "volume" not in result
    assert (
        suspended_status(
            [*ROW[:6], "0", "0", *ROW[8:]],
            code="000001",
            provider_code="sz.000001",
            day=DAY,
            observed_at=STAMP,
        )
        is None
    )


@pytest.mark.parametrize(
    "index,value", [(0, "2026-10-12"), (1, "sh.000001"), (10, ""), (11, ""), (2, "NaN")]
)
def test_invalid_suspension_is_not_admitted(index: int, value: str) -> None:
    row = list(ROW)
    row[index] = value
    with pytest.raises(ValueError):
        suspended_status(row, code="000001", provider_code="sz.000001", day=DAY, observed_at=STAMP)


def test_actual_provider_projection_does_not_retry_a_proven_suspension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.pipelines import lane_runner as lanes

    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", POLICY)
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.setenv("ASTOCK_HOME", str(tmp_path / "unavailable-current-authority"))
    monkeypatch.setenv("QUANTHUS_COLLECTOR_SHA", "a" * 40)
    monkeypatch.setenv("HERMES_ODS_RAW_CAPTURE_ROOT", str(tmp_path))
    monkeypatch.setattr(lanes, "_baostock_login_with_retry", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        lanes, "_baostock_query_day_with_retry", lambda *_a, **_k: ([ROW], "0", "ok", 1, [])
    )
    monkeypatch.setitem(__import__("sys").modules, "baostock", SimpleNamespace(logout=lambda: None))
    bars, status, logs = lanes._baostock_fetch_day(
        ["000001"], date.fromisoformat(DAY), missing_set_retry_delay_seconds=0
    )
    assert bars == [] and len(status) == 1 and len(logs) == 1
    assert status[0]["isST"] == "1"
    assert len(list(tmp_path.glob("*.json.gz"))) == 1


def fixture_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    from quanthus.pipelines.p10_delivery_common import atomic, file_digest

    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", POLICY)
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.delenv("HERMES_ODS_SYMBOL_LIMIT", raising=False)
    monkeypatch.setenv("ASTOCK_HOME", str(tmp_path / "unavailable-current-authority"))
    monkeypatch.setenv("QUANTHUS_COLLECTOR_SHA", "a" * 40)
    parent = tmp_path / "shadow/attempts/market-daily-core" / DAY
    source = parent / "failed"
    landing = source / "landing"
    landing.mkdir(parents=True)
    write_parquet(
        landing / "daily_status_observation_delta.parquet",
        [{"security_id": "000002", "market_date": DAY, "tradestatus": "1", "isST": "0"}],
    )
    write_parquet(
        landing / "daily_ohlcv_none_delta.parquet", [{"security_id": "000002", "market_date": DAY}]
    )
    write_parquet(
        landing / "ref_security_master.parquet",
        [
            {"security_id": "000001", "tradestatus": "0"},
            {"security_id": "000002", "tradestatus": "1"},
        ],
    )
    from quanthus.pipelines.p10_delivery_common import digest, encoded

    universe_digest = digest(encoded(["000001", "000002"]))
    __import__("shutil").copyfile(
        landing / "ref_security_master.parquet", landing / "stock_list.parquet"
    )
    receipt = capture(
        root=landing / "original-responses",
        request={
            "api": "query_history_k_data_plus",
            "code": "sz.000001",
            "fields": FIELDS,
            "frequency": "d",
            "adjustflag": "3",
            "start_date": DAY,
            "end_date": DAY,
        },
        raw=json.dumps({"fields": FIELDS.split(","), "rows": [ROW], "error_code": "0"}).encode(),
        started_at=STAMP,
        completed_at=STAMP,
        status="0",
    )
    (landing / "original-responses.tar.gz").write_bytes(b"synthetic immutable archive")
    for name in ["trading_calendar", "index_daily_delta", "valuation_daily_delta"]:
        write_parquet(landing / (name + ".parquet"), [{"market_date": DAY}])
    atomic(landing / "acquisition_log.json", {"requests": []})
    atomic(
        landing / "source_universe.json",
        {
            "market_date": DAY,
            "codes": ["000001", "000002"],
            "universe_digest": universe_digest,
            "policy_id": POLICY,
            "population": 2,
            "historical_pit_authorized": False,
            "warehouse_observed_at": STAMP,
            "files": {
                "security_master.parquet": file_digest(landing / "ref_security_master.parquet"),
                "stock_list.parquet": file_digest(landing / "stock_list.parquet"),
            },
        },
    )
    atomic(
        landing / "acquisition_quality.json",
        {
            "universe_digest": universe_digest,
            "original_responses_sha256": file_digest(landing / "original-responses.tar.gz"),
            "raw_members": [
                {
                    "path": receipt["capture_file"],
                    "sha256": file_digest(landing / "original-responses" / receipt["capture_file"]),
                }
            ],
        },
    )
    datasets = [
        {
            "name": name.removesuffix(".parquet")
            .removesuffix(".json")
            .replace("ref_security_master", "security_master"),
            "path": str(landing / name),
            "sha256": file_digest(landing / name),
            "status": "failed" if name == "daily_status_observation_delta.parquet" else "ready",
        }
        for name in [
            "daily_status_observation_delta.parquet",
            "daily_ohlcv_none_delta.parquet",
            "ref_security_master.parquet",
            "source_universe.json",
            "acquisition_quality.json",
            "stock_list.parquet",
            "trading_calendar.parquet",
            "index_daily_delta.parquet",
            "valuation_daily_delta.parquet",
            "acquisition_log.json",
        ]
    ]
    atomic(
        landing / "lane_status.json",
        {
            "market_date": DAY,
            "lane_id": "market-daily-core",
            "stream": None,
            "datasets": datasets,
            "schedule_id": "synthetic",
            "schedule_contract_revision": "synthetic",
            "schedule_config_sha256": "sha256:" + "b" * 64,
        },
    )
    monkeypatch.setattr(
        __import__("quanthus.pipelines.p10_suspended_status_recovery", fromlist=["shutil"]).shutil,
        "disk_usage",
        lambda _p: SimpleNamespace(free=8 * 1024**3),
    )
    return source, parent / "new"


def test_recovery_reuses_exact_originals_and_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyarrow.parquet as pq

    from quanthus.pipelines import p10_suspended_status_recovery as recovery
    from quanthus.pipelines.p10_delivery_common import file_digest

    source, output = fixture_attempt(tmp_path, monkeypatch)
    before = file_digest(source / "landing/lane_status.json")

    result = recovery.recover(source=source, output=output, revision="a" * 40)
    assert result["supplier_calls"] == 0 and result["recovered_status_count"] == 1
    assert file_digest(source / "landing/lane_status.json") == before
    rows = (
        pq.ParquetFile(output / "landing/daily_status_observation_delta.parquet").read().to_pylist()
    )
    assert rows[-1]["tradestatus"] == "0"
    assert file_digest(source / "landing/daily_ohlcv_none_delta.parquet") == file_digest(
        output / "landing/daily_ohlcv_none_delta.parquet"
    )
    release = Path(result["release_dir"])
    assert (release / "READY").is_file()
    assert file_digest(release / "artifacts/source_universe.json") == file_digest(
        source / "landing/source_universe.json"
    )
    assert not (tmp_path / "unavailable-current-authority").exists()


def test_real_publisher_withholds_ready_when_copied_capture_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.pipelines import p10_suspended_status_recovery as recovery

    source, output = fixture_attempt(tmp_path, monkeypatch)
    publish = recovery.lanes.publish_ods_release

    def tampered_publish(**kwargs: object) -> object:
        check = kwargs["before_ready"]

        def tamper_and_check() -> None:
            raw = next((output / "landing/original-responses").glob("*.json.gz"))
            raw.write_bytes(raw.read_bytes() + b"changed")
            check()

        return publish(**{**kwargs, "before_ready": tamper_and_check})

    monkeypatch.setattr(recovery.lanes, "publish_ods_release", tampered_publish)
    with pytest.raises(ValueError, match="RECOVERY_COPIED_CAPTURE_CHANGED"):
        recovery.recover(source=source, output=output, revision="a" * 40)
    assert not list((tmp_path / "shadow/releases").rglob("READY"))


def test_open_security_cannot_be_recovered_as_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.pipelines import p10_suspended_status_recovery as recovery
    from quanthus.pipelines.p10_delivery_common import atomic, file_digest, object_at

    source, output = fixture_attempt(tmp_path, monkeypatch)
    landing = source / "landing"
    write_parquet(
        landing / "ref_security_master.parquet",
        [
            {"security_id": "000001", "tradestatus": "1"},
            {"security_id": "000002", "tradestatus": "1"},
        ],
    )
    state = object_at(landing / "lane_status.json")
    for item in state["datasets"]:
        if item["name"] == "security_master":
            item["sha256"] = file_digest(landing / "ref_security_master.parquet")
    atomic(landing / "lane_status.json", state)
    with pytest.raises(ValueError, match="MISSING_OPEN"):
        recovery.recover(source=source, output=output, revision="a" * 40)
    assert not output.exists()
