"""Base 2.4 native-5m admission is exact, source-bound and fail closed."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_quality import (
    Native5mQualityError,
    assess_native_5m_release,
    assess_native_5m_series,
)

POLICY = Path("config/pipelines/base24-native5m-quality.v1.json").read_bytes()


def _buckets(day: str) -> list[datetime]:
    morning = datetime.fromisoformat(day + "T09:35:00")
    afternoon = datetime.fromisoformat(day + "T13:05:00")
    return [morning + timedelta(minutes=5 * i) for i in range(24)] + [
        afternoon + timedelta(minutes=5 * i) for i in range(24)
    ]


def _write_release(root: Path, day: str = "2026-09-22") -> Path:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    rows = [
        {
            "stock_code": "000001",
            "ts": stamp,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 0.0,
            "amount": 0.0,
            "security_id": "SZSE:000001",
            "bar_size": "5m",
            "market_date": day,
            "acquired_at": day + "T18:00:00+08:00",
        }
        for stamp in _buckets(day)
    ]
    pq.write_table(pa.Table.from_pylist(rows), artifacts / "intraday_5m_bars.parquet")
    coverage = [
        {
            "security_id": "000001",
            "status": "ready",
            "row_count": 48,
            "reason": "ok",
            "observed_maximum": day + " 15:00:00",
            "source": None,
            "evidence_date": None,
            "knowledge_cutoff": None,
            "artifact_sha256": None,
            "tradestatus": None,
        },
        {
            "security_id": "000002",
            "status": "justified_no_data",
            "row_count": 0,
            "reason": "daily_status_not_tradable",
            "observed_maximum": None,
            "source": "daily_status_observation_delta.parquet",
            "evidence_date": day,
            "knowledge_cutoff": day + "T18:00:00+08:00",
            "artifact_sha256": "sha256:" + "1" * 64,
            "tradestatus": "0",
        },
    ]
    pq.write_table(pa.Table.from_pylist(coverage), artifacts / "intraday_5m_coverage.parquet")
    meta = {
        "bar_size": "5m",
        "market_date": day,
        "coverage_ok": True,
        "session_complete": True,
        "session_policy_version": "cn-a-share-trading-session.v1",
        "expected_rows_per_ready_symbol": 48,
        "ready_symbols": 1,
        "justified_no_data": 1,
        "unexplained_missing": 0,
        "universe_size": 2,
        "may_derive_from": None,
    }
    (artifacts / "intraday_5m_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    datasets = []
    for name, filename in [
        ("intraday_bars", "intraday_5m_bars.parquet"),
        ("intraday_coverage", "intraday_5m_coverage.parquet"),
        ("intraday_meta", "intraday_5m_meta.json"),
    ]:
        path = artifacts / filename
        datasets.append(
            {
                "name": name,
                "path": str(path),
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "row_count": (
                    48 if name == "intraday_bars" else 2 if name == "intraday_coverage" else 1
                ),
                "status": "ready",
                "watermark": day,
            }
        )
    (artifacts / "lane_status.json").write_text(
        json.dumps({"market_date": day, "stream": "5m", "datasets": datasets}),
        encoding="utf-8",
    )
    return artifacts


def test_exact_release_and_evidenced_no_data_meet_minimum_r2(tmp_path):
    result = assess_native_5m_release(_write_release(tmp_path / "one"), POLICY)
    assert result["rating"] == "R2"
    assert result["ready_security_days"] == 1
    assert result["justified_no_data_security_days"] == 1
    assert result["bucket_rows"] == 48
    assert result["consumer_admission"] == "GAP_AWARE_NON_EXECUTION_ONLY"


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("bucket", "BUCKET_GRID"),
        ("duplicate", "DUPLICATE"),
        ("derived", "DERIVED"),
        ("unproved_empty", "NO_DATA_EVIDENCE"),
        ("hash", "ARTIFACT_HASH"),
    ],
)
def test_release_defects_fail_closed(tmp_path, defect, reason):
    artifacts = _write_release(tmp_path / defect)
    if defect in {"bucket", "duplicate"}:
        path = artifacts / "intraday_5m_bars.parquet"
        rows = pq.read_table(path).to_pylist()
        rows[-1]["ts"] = rows[-2]["ts"] if defect == "duplicate" else datetime(2026, 9, 22, 14, 59)
        pq.write_table(pa.Table.from_pylist(rows), path)
        status = json.loads((artifacts / "lane_status.json").read_text())
        status["datasets"][0]["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        (artifacts / "lane_status.json").write_text(json.dumps(status))
    elif defect == "derived":
        meta = json.loads((artifacts / "intraday_5m_meta.json").read_text())
        meta["may_derive_from"] = "1m"
        (artifacts / "intraday_5m_meta.json").write_text(json.dumps(meta))
        status = json.loads((artifacts / "lane_status.json").read_text())
        path = artifacts / "intraday_5m_meta.json"
        status["datasets"][2]["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        (artifacts / "lane_status.json").write_text(json.dumps(status))
    elif defect == "unproved_empty":
        path = artifacts / "intraday_5m_coverage.parquet"
        rows = pq.read_table(path).to_pylist()
        rows[1]["artifact_sha256"] = None
        pq.write_table(pa.Table.from_pylist(rows), path)
        status = json.loads((artifacts / "lane_status.json").read_text())
        status["datasets"][1]["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        (artifacts / "lane_status.json").write_text(json.dumps(status))
    else:
        with (artifacts / "intraday_5m_bars.parquet").open("ab") as stream:
            stream.write(b"changed")
    with pytest.raises(Native5mQualityError, match=reason):
        assess_native_5m_release(artifacts, POLICY)


def test_series_rejects_duplicate_or_missing_required_dates(tmp_path):
    one = _write_release(tmp_path / "one")
    two = _write_release(tmp_path / "two")
    with pytest.raises(Native5mQualityError, match="DUPLICATE_MARKET_DATE"):
        assess_native_5m_series([one, two], ["2026-09-22"], POLICY)
    with pytest.raises(Native5mQualityError, match="REQUIRED_DATE_MISSING"):
        assess_native_5m_series([one], ["2026-09-22", "2026-09-23"], POLICY)
