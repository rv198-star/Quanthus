"""Synthetic tests: source-derived auction repair must never use a daily residual."""

import base64
import json
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.closing_auction_source import (
    CONTRACT,
    PHASE,
    PROVIDER,
    observed,
    phase,
    replay,
    sha,
)
from quanthus.pipelines.securities_warehouse.native5m_daily_source import m5_grid, native_m5

DAY = "2026-11-03"


def dump(value):
    return json.dumps(value, sort_keys=True).encode()


def capture(raw, url):
    return {
        "url": url,
        "raw_base64": base64.b64encode(raw).decode(),
        "sha256": sha(raw),
        "started_at": "2026-11-04T00:00:00+08:00",
        "completed_at": "2026-11-04T00:00:01+08:00",
    }


def envelope(code="600061"):
    rows = [
        [DAY, stamp + "00000", "sh." + code, "10", "10", "10", "10", "1", "10"]
        for stamp in m5_grid(DAY)
    ]
    base = dump(
        {
            "provider": "baostock-native-5m",
            "frequency": "5",
            "adjustflag": "3",
            "code": "sh." + code,
            "market_date": DAY,
            "fields": ["date", "time", "code", "open", "high", "low", "close", "volume", "amount"],
            "rows": rows,
        }
    )
    prefix = "https://stock.gtimg.cn/data/index.php?appn=detail&c=sh" + code
    count = ("v_detail_time_sh" + code + "=" + json.dumps([20261103, "14:56:00~15:05:00"])).encode()
    ticks = (
        "v_detail_data_sh"
        + code
        + "="
        + json.dumps(
            [0, "1/14:56:00/10/0/1/100/B|2/15:00:02/11/1/2/2200/M|3/15:05:00/11/0/10000/11000000/B"]
        )
    ).encode()
    e = {
        "provider": PROVIDER,
        "contract": CONTRACT,
        "stock_code": code,
        "market_date": DAY,
        "base_raw_base64": base64.b64encode(base).decode(),
        "base_raw_sha256": sha(base),
        "evidence": {
            "count": capture(count, prefix + "&action=datacount"),
            "ticks": capture(ticks, prefix + "&action=data&p=0"),
        },
    }
    if code.startswith("688"):
        body = dump(
            {
                "code": 0,
                "data": {
                    "sh" + code: {
                        "data": {
                            "date": "20261103",
                            "data": [
                                "1459 10 1000 10000.00",
                                "1500 11 1203 12233.00",
                                "1505 11 9999 999999.00",
                            ],
                        }
                    }
                },
            }
        )
        e["evidence"]["minute"] = capture(
            body, "https://ifzq.gtimg.cn/appstock/app/minute/query?code=sh" + code
        )
    return e


def test_legacy_and_only_terminal_changes():
    e = envelope()
    base = base64.b64decode(e["base_raw_base64"])
    old = native_m5(base, "600061", DAY)
    new = native_m5(dump(e), "600061", DAY)
    stamps = sorted(old)
    assert all(new[t] == old[t] for t in stamps[:-1])
    end = new[stamps[-1]]
    assert (end["open"], end["high"], end["low"], end["close"]) == (10, 11, 10, 11)
    assert end["volume_shares"] == 201
    assert sum(x["volume_shares"] for x in new.values()) == 248
    assert end["amount"] == 2210
    assert phase(dump(e), stamps[-1]) == PHASE
    assert phase(dump(e), stamps[0]) == "continuous"
    assert len(old) == len(new) == 48


def test_kcb_uses_exact_cumulative_shares_not_rounded_tick_lots():
    e = envelope("688303")
    actual = observed(e, "688303", DAY)
    assert actual["volume"] == 203
    assert actual["amount"] == Decimal("2233.00")
    assert replay(dump(e), "688303", DAY)["202611031500"]["volume_shares"] == 204


@pytest.mark.parametrize(
    "kind", ["hash", "url", "date", "symbol", "clock", "base_hash", "provider"]
)
def test_rejects_broken_evidence_bindings(kind):
    e = envelope()
    if kind == "hash":
        e["evidence"]["ticks"]["sha256"] = "sha256:" + "0" * 64
    elif kind == "url":
        e["evidence"]["ticks"]["url"] += "&other=1"
    elif kind == "date":
        e["market_date"] = "2026-11-02"
    elif kind == "symbol":
        e["stock_code"] = "600706"
    elif kind == "clock":
        e["evidence"]["ticks"]["started_at"] = "2026-11-05T00:00:00+08:00"
    elif kind == "base_hash":
        e["base_raw_sha256"] = "sha256:" + "0" * 64
    elif kind == "provider":
        e["provider"] = "unapproved-provider"
    with pytest.raises(ValueError):
        replay(dump(e), "600061", DAY)


def test_auction_must_be_observed_once_and_cannot_use_postmarket():
    e = envelope()
    entry = e["evidence"]["ticks"]
    raw = base64.b64decode(entry["raw_base64"]).replace(b"15:00:02", b"15:05:02")
    entry.update(raw_base64=base64.b64encode(raw).decode(), sha256=sha(raw))
    with pytest.raises(ValueError, match="SINGLE_MATCH"):
        replay(dump(e), "600061", DAY)


def test_incomplete_base_grid_is_not_filled():
    e = envelope()
    base = json.loads(base64.b64decode(e["base_raw_base64"]))
    base["rows"].pop(0)
    raw = dump(base)
    e.update(base_raw_base64=base64.b64encode(raw).decode(), base_raw_sha256=sha(raw))
    with pytest.raises(ValueError, match="COMPLETE_BASE_GRID"):
        replay(dump(e), "600061", DAY)


def held_auction_fixture(tmp_path):
    import gzip

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
    from quanthus.pipelines.securities_warehouse.native5m_daily_source import (
        KIND,
        _digest,
        minute_schema,
    )

    attempt = tmp_path / "attempt"
    source = attempt / "native"
    source.mkdir(parents=True)
    e = envelope()
    original = base64.b64decode(e["base_raw_base64"])
    raw = attempt / "capture-5m/shard-000/raw/600061.json.gz"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(gzip.compress(original))
    market_sha, events_sha = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    ref = {
        "kind": "p10_complete_daily_job_reference_v3",
        "market_date": DAY,
        "source_ods_manifest_sha256": market_sha,
        "event_ods_manifest_sha256": events_sha,
        "securities": {"600061": {"tradestatus": "1", "close": "11", "volume": "248"}},
        "population": 1,
        "universe_policy_id": "p10-acquisition-prevention-v1",
        "universe_digest": _digest(["600061"]),
    }
    ref["reference_digest"] = _digest(ref)
    (source / "daily-reference.json").write_bytes(canonical(ref))
    status = {
        "stock_code": "600061",
        "market_date": DAY,
        "quality_grade": "R3",
        "quality_reason": "missing_or_wrong_terminal_close",
        "missing_actual_minutes": [],
        "raw_sha256": sha(original),
        "reference_ods_manifest_sha256": market_sha,
    }
    (source / "quality-status.jsonl").write_bytes(canonical(status) + b"\n")
    pq.write_table(
        pa.Table.from_pylist([], schema=minute_schema()), source / "minute-real-observed.parquet"
    )
    files = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": file_sha(p)}
        for p in sorted(source.iterdir())
    ]
    m = {
        "kind": KIND,
        "schema_version": 1,
        "policy_id": "p10-trading-day-complete-daily-v1",
        "market_date": DAY,
        "interval": "5m",
        "execution_authorized": False,
        "files": files,
        "content_digest": _digest(files),
        "reference_digest": ref["reference_digest"],
        "source_ods_manifest_sha256": market_sha,
        "grades": {"R3": 1},
        "rows_per_grade": {},
        "research_ready": False,
    }
    (source / "manifest.json").write_bytes(canonical(m))
    (source / "READY").write_text(file_sha(source / "manifest.json") + "\n")
    receipt = {
        "market_date": DAY,
        "status": "CORE_READY_5M_HELD_SOURCE_QUALITY",
        "native_source": str(source),
        "native_manifest_sha256": file_sha(source / "manifest.json"),
    }
    (attempt / "receipt.json").write_bytes(canonical(receipt))
    evidence = tmp_path / "captures"
    evidence.mkdir()
    index = {"market_date": DAY, "files": {}}
    for kind, name in [("count", "tick-count-600061.raw"), ("ticks", "tick-last-600061.raw")]:
        entry = e["evidence"][kind]
        (evidence / name).write_bytes(base64.b64decode(entry["raw_base64"]))
        index["files"][name] = {k: v for k, v in entry.items() if k != "raw_base64"}
    (evidence / "CAPTURE.json").write_bytes(canonical(index))
    return attempt, evidence, source, receipt


def test_builder_then_full_cold_replay(tmp_path):
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.closing_auction_repair import build
    from quanthus.pipelines.securities_warehouse.native5m_daily_source import verify_native5m_source

    attempt, evidence, source, receipt = held_auction_fixture(tmp_path)
    market_sha, events_sha = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    output = tmp_path / "repaired"
    result = build(attempt, evidence, output, "c" * 40)
    checked = verify_native5m_source(
        output,
        result["manifest_sha256"],
        day=DAY,
        daily={"600061": {"is_tradable": True, "close": Decimal(11), "volume": Decimal(248)}},
        daily_sources={"market_daily_core": market_sha, "calendar_daily_events": events_sha},
    )
    assert checked["grades"] == {"R0": 1}
    assert checked["rows"] == 48
    assert file_sha(source / "manifest.json") == receipt["native_manifest_sha256"]


def test_rebind_rejects_compound_without_silently_losing_auction():
    from quanthus.pipelines.securities_warehouse.daily_base24_native_rebind import qualify

    with pytest.raises(ValueError, match="COMPOUND_SOURCE_REQUIRES_AUCTION_REPLAY"):
        qualify(dump(envelope()), "600061", DAY, {})
