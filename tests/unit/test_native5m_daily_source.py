from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import native5m_daily_source as source
from quanthus.pipelines.securities_warehouse.base22 import canonical


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def encoded(value):
    return canonical(value) + b"\n"


def seal(root, day="2026-10-08"):
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in {"manifest.json", "READY"}:
            entries.append(
                {
                    "path": p.relative_to(root).as_posix(),
                    "bytes": p.stat().st_size,
                    "sha256": digest(p.read_bytes()),
                }
            )
    manifest = {
        "kind": source.KIND,
        "schema_version": 1,
        "policy_id": "p10-trading-day-complete-daily-v1",
        "market_date": day,
        "interval": "5m",
        "execution_authorized": False,
        "code_revision": "e" * 40,
        "grades": {"R0": 1, "SUSPENDED": 1},
        "rows_per_grade": {"R0": 48},
        "research_ready": True,
        "reference_digest": json.loads((root / "daily-reference.json").read_text())[
            "reference_digest"
        ],
        "source_ods_manifest_sha256": "sha256:" + "a" * 64,
        "sealed_at": (datetime.fromisoformat(day) + timedelta(days=1)).isoformat() + "+00:00",
        "files": entries,
        "content_digest": digest(encoded(entries)),
    }
    (root / "manifest.json").write_bytes(encoded(manifest))
    sha = digest((root / "manifest.json").read_bytes())
    (root / "READY").write_text(sha + "\n")
    return sha


@pytest.fixture
def original(tmp_path, day="2026-10-08"):
    root = tmp_path / "native"
    root.mkdir()
    (root / "ods/raw").mkdir(parents=True)
    labels = []
    for hour, minute in [(9, 35), (13, 5)]:
        labels += [
            (
                datetime.fromisoformat(day).replace(hour=hour, minute=minute)
                + timedelta(minutes=5 * i)
            )
            for i in range(24)
        ]
    rows = [
        [
            day,
            x.strftime("%Y%m%d%H%M%S") + "000",
            "sz.000001",
            "2.0000000001",
            "2.0000000001",
            "2.0000000001",
            "2.0000000001",
            "100",
            "200",
        ]
        for x in labels
    ]
    raw = encoded(
        {
            "provider": "baostock-native-5m",
            "frequency": "5",
            "adjustflag": "3",
            "code": "sz.000001",
            "market_date": day,
            "fields": source.FIELDS.split(","),
            "rows": rows,
        }
    )
    (root / "ods/raw/000001.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    statuses = [
        {
            "stock_code": "000001",
            "market_date": day,
            "quality_grade": "R0",
            "quality_reason": "native_48_exact_daily",
            "raw_sha256": digest(raw),
            "source_acquired_at_or_after": day + "T18:00:00+08:00",
            "reference_ods_manifest_sha256": "sha256:" + "a" * 64,
            "formally_ingested": True,
            "default_research_admitted": True,
        },
        {
            "stock_code": "000002",
            "market_date": day,
            "quality_grade": "SUSPENDED",
            "quality_reason": "sealed_daily_suspension_price_unavailable",
            "raw_sha256": None,
            "reference_ods_manifest_sha256": "sha256:" + "a" * 64,
            "formally_ingested": False,
            "default_research_admitted": False,
        },
    ]
    (root / "quality-status.jsonl").write_text("".join(json.dumps(x) + "\n" for x in statuses))
    daily = {
        "000001": {
            "close": Decimal("2.0000000001"),
            "volume": Decimal("4800"),
            "is_tradable": True,
        },
        "000002": {
            "close": None,
            "volume": None,
            "is_tradable": False,
            "daily_price_observation_state": "unavailable_suspended",
        },
    }
    ref = {
        "kind": "p10_complete_daily_job_reference_v3",
        "policy_id": "p10-trading-day-complete-daily-v1",
        "market_date": day,
        "population": 2,
        "source_ods_manifest_sha256": "sha256:" + "a" * 64,
        "event_ods_manifest_sha256": "sha256:" + "b" * 64,
        "universe_policy_id": "p10-acquisition-prevention-v1",
        "universe_digest": digest(encoded(sorted(daily))),
        "universe_manifest_sha256": "sha256:" + "c" * 64,
        "securities": {
            "000001": {"tradestatus": "1", "close": "2.0000000001", "volume": "4800"},
            "000002": {
                "tradestatus": "0",
                "close": None,
                "volume": None,
                "daily_price_observation_state": "unavailable_suspended",
            },
        },
    }
    ref["reference_digest"] = digest(encoded(ref))
    (root / "daily-reference.json").write_bytes(encoded(ref))
    values = []
    for x in labels:
        values.append(
            {
                "stock_code": "000001",
                "market_date": day,
                "source_ts": x.isoformat(),
                "exchange_session_phase": "continuous",
                **{k: Decimal("2.0000000001") for k in ["open", "high", "low", "close"]},
                "volume_shares": Decimal("100"),
                "amount": Decimal("200"),
                "raw_sha256": digest(raw),
                "quality_grade": "R0",
                "quality_reason": "native_48_exact_daily",
                "original_source_observed": True,
                "default_research_admitted": True,
            }
        )
    pq.write_table(
        pa.Table.from_pylist(values, schema=source.minute_schema()),
        root / "minute-real-observed.parquet",
    )
    sha = seal(root, day)
    inputs = {
        "market_daily_core": "sha256:" + "a" * 64,
        "calendar_daily_events": "sha256:" + "b" * 64,
    }
    return root, sha, daily, inputs


def verify(original):
    root, sha, daily, inputs = original
    return source.verify_native5m_source(
        root, sha, day="2026-10-08", daily=daily, daily_sources=inputs
    )


def test_existing_native_originals_replay_without_1m_or_collection(original):
    result = verify(original)
    assert result["rows"] == 48 and result["population"] == 2
    assert result["grades"] == {"R0": 1, "SUSPENDED": 1}
    assert result["provider_acquisition"] == "NOT_INFERRED"
    assert result["research_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("adjustflag", "1"),
        ("frequency", "1"),
        ("provider", "derived-from-1m"),
        ("code", "sh.000001"),
        ("market_date", "2026-10-09"),
    ],
)
def test_native_parser_retains_exact_source_scope(original, field, value):
    root, _, _, _ = original
    raw = json.loads(gzip.decompress((root / "ods/raw/000001.json.gz").read_bytes()))
    raw[field] = value
    with pytest.raises(ValueError, match="provenance"):
        source.native_m5(encoded(raw), "000001", "2026-10-08")


@pytest.mark.parametrize(
    "mutation",
    [
        "changed-source",
        "extra-file",
        "repacked-price",
        "repacked-time",
        "repacked-seconds",
        "unknown-status",
        "naive-time",
        "future-time",
        "daily-conflict",
        "wrong-source-binding",
        "wrong-day",
    ],
)
def test_source_or_dependency_mutations_fail_closed(original, mutation):
    root, sha, daily, inputs = original
    if mutation == "changed-source":
        (root / "minute-real-observed.parquet").write_bytes(b"corrupt")
    elif mutation == "extra-file":
        (root / "unlisted.parquet").write_bytes(b"extra")
    elif mutation in {"repacked-price", "repacked-time", "repacked-seconds"}:
        p = root / "minute-real-observed.parquet"
        t = pq.read_table(p)
        rows = t.to_pylist()
        rows[0]["close"] = (
            Decimal("2.0000000002") if mutation == "repacked-price" else rows[0]["close"]
        )
        if mutation == "repacked-time":
            rows[0]["source_ts"] = "2026-10-08T09:36:00"
        elif mutation == "repacked-seconds":
            rows[0]["source_ts"] = "2026-10-08T09:35:01"
        pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
        sha = seal(root)
    elif mutation in {"unknown-status", "naive-time", "future-time"}:
        p = root / "quality-status.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()]
        if mutation == "unknown-status":
            rows[0]["quality_grade"] = "UNASSESSED"
        else:
            rows[0]["source_acquired_at_or_after"] = (
                "2026-10-10T18:00:00+08:00" if mutation == "future-time" else "2026-10-08T18:00:00"
            )
        p.write_text("".join(json.dumps(x) + "\n" for x in rows))
        sha = seal(root)
    elif mutation == "daily-conflict":
        daily["000001"]["volume"] = Decimal("4801")
    elif mutation == "wrong-source-binding":
        inputs["market_daily_core"] = "sha256:" + "d" * 64
    elif mutation == "wrong-day":
        with pytest.raises(ValueError):
            source.verify_native5m_source(
                root, sha, day="2026-10-09", daily=daily, daily_sources=inputs
            )
        return
    with pytest.raises(ValueError):
        source.verify_native5m_source(
            root, sha, day="2026-10-08", daily=daily, daily_sources=inputs
        )
