"""Contract tests for the bounded no-trade regrade continuation (#692).

The continuation regrades frozen original captures with the pinned collector,
reuses the frozen core and sources, and never recollects. Only R3 entries whose
sole defect is a source-observed all-zero no-trade bar are eligible.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle
from quanthus.pipelines.securities_warehouse import daily_no_trade_regrade as regrade
from quanthus.pipelines.securities_warehouse import native5m_daily_source as native

DAY = date(2026, 10, 20)
CODE = "300462"
VENDOR = "sz." + CODE


def raw_bytes(
    zero_index: int | None = 0,
    *,
    drop_index: int | None = None,
    zero_close_with_volume: bool = False,
) -> bytes:
    rows = []
    for i, label in enumerate(native.m5_grid(str(DAY))):
        if i == drop_index:
            continue
        if zero_index is not None and i == zero_index:
            values = ["0.0000"] * 4 + ["0", "0.0000"]
        elif i == 47:
            values = ["10.10", "10.20", "10.03", "10.19", "300", "3057.0000"]
        else:
            values = ["10.20", "10.20", "10.20", "10.20", "200", "2040.0000"]
        if zero_close_with_volume and i == 5:
            values = ["10.20", "10.20", "10.20", "0.0000", "200", "2040.0000"]
        rows.append([str(DAY), label + "00000", VENDOR, *values])
    return (
        native.canonical(
            dict(
                provider=native.PROVIDER,
                frequency="5",
                adjustflag="3",
                code=VENDOR,
                market_date=str(DAY),
                fields=native.FIELDS.split(","),
                rows=rows,
            )
        )
        + b"\n"
    )


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def held_fixture(tmp_path, *, raw: bytes | None = None, reason: str = "invalid_native_5m_source"):
    raw = raw if raw is not None else raw_bytes()
    root = tmp_path
    previous = root / (str(DAY) + "-20261020T093000000000Z")
    (previous / "core").mkdir(parents=True)
    market = root / "sources/daily" / str(DAY) / "market"
    events = root / "sources/daily" / str(DAY) / "events"
    for source in (market, events):
        source.mkdir(parents=True)
        cycle.write(source / "release.json", {"lane": source.name})
    reference = {
        "kind": "p10_complete_daily_job_reference_v3",
        "market_date": str(DAY),
        "securities": {
            CODE: {"tradestatus": "1", "close": "10.19", "volume": "9500"},
            "600000": {"tradestatus": "1", "close": "12.50", "volume": "10000"},
        },
    }
    cycle.write(previous / "daily-reference.json", reference)
    capture = previous / "capture-5m/shard-000/raw"
    capture.mkdir(parents=True)
    (capture / (CODE + ".json.gz")).write_bytes(gzip.compress(raw, mtime=0))
    source = root / "native-held"
    source.mkdir()
    cycle.write(source / "manifest.json", {"research_ready": False, "grades": {"R3": 1, "R0": 1}})
    (source / "daily-reference.json").write_bytes((previous / "daily-reference.json").read_bytes())
    statuses = [
        {
            "stock_code": CODE,
            "market_date": str(DAY),
            "quality_grade": "R3",
            "quality_reason": reason,
            "raw_sha256": sha(raw),
        },
        {
            "stock_code": "600000",
            "market_date": str(DAY),
            "quality_grade": "R0",
            "quality_reason": "native_48_exact_daily",
            "raw_sha256": sha(b"other"),
        },
    ]
    (source / "quality-status.jsonl").write_text("".join(json.dumps(s) + "\n" for s in statuses))
    cycle.write(previous / "market-source.json", {"release_dir": str(market)})
    cycle.write(previous / "events-source.json", {"release_dir": str(events)})
    old = {
        "kind": cycle.KIND,
        "market_date": str(DAY),
        "status": "CORE_READY_5M_HELD_SOURCE_QUALITY",
        "core": str(previous / "core"),
        "native_source": str(source),
        "native_manifest_sha256": cycle.file_sha(source / "manifest.json"),
        "source_manifest_sha256": {
            "market_daily_core": cycle.file_sha(market / "release.json"),
            "calendar_daily_events": cycle.file_sha(events / "release.json"),
        },
        "base_manifest_sha256": "sha256:" + "b" * 64,
        "config_sha256": "sha256:" + "c" * 64,
        "pipeline_revision": "a" * 40,
        "collector_revision": "f" * 40,
        "execution_authorized": False,
        "consumer_admission": "NOT_GRANTED",
    }
    cycle.write(previous / "receipt.json", old)
    cycle.write(previous / "registration.json", {"market_date": str(DAY)})
    config = {
        "root": str(root),
        "base": str(root / "base"),
        "release_config": str(root / "policy"),
        "pipeline_revision": "d" * 40,
        "base_manifest_sha256": old["base_manifest_sha256"],
        "release_config_sha256": old["config_sha256"],
        "recovery_mode": True,
    }
    return root, previous, source, config, old


def test_eligible_held_no_trade_state_returns_frozen_inputs(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    value, market, events, captures, reference = regrade.continuation(config, DAY, old)
    assert value == old
    assert captures == previous / "capture-5m"
    assert reference == previous / "daily-reference.json"
    assert market.parent == events.parent


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda c, p, s, o: c.update(recovery_mode=False), "NOT_ELIGIBLE"),
        (lambda c, p, s, o: o.update(status="CORE_READY_5M_FAILED"), "NOT_ELIGIBLE"),
        (lambda c, p, s, o: o.update(base_manifest_sha256="sha256:" + "9" * 64), "NOT_ELIGIBLE"),
        (
            lambda c, p, s, o: cycle.atomic(p / "receipt.json", {**o, "extra": True}),
            "RECEIPT_CHANGED",
        ),
        (
            lambda c, p, s, o: cycle.atomic(s / "manifest.json", {"research_ready": True}),
            "SOURCE_CHANGED",
        ),
        (lambda c, p, s, o: (s / "manifest.json").write_bytes(b"{}"), "SOURCE_CHANGED"),
        (lambda c, p, s, o: (s / "daily-reference.json").write_bytes(b"{}"), "REFERENCE_DIFFERS"),
    ],
)
def test_ineligible_or_changed_state_fails_closed(tmp_path, mutate, match):
    root, previous, source, config, old = held_fixture(tmp_path)
    mutate(config, previous, source, old)
    with pytest.raises(ValueError, match=match):
        regrade.continuation(config, DAY, old)


def test_second_continuation_for_same_date_is_rejected(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    other = root / (str(DAY) + "-20261020T131500000000Z")
    other.mkdir()
    cycle.write(other / "no-trade-regrade-continuation.json", {"market_date": str(DAY)})
    with pytest.raises(ValueError, match="NOT_ELIGIBLE"):
        regrade.continuation(config, DAY, old)


def test_other_held_reason_keeps_original_disposition(tmp_path):
    root, previous, source, config, old = held_fixture(
        tmp_path, reason="missing_or_wrong_terminal_close"
    )
    with pytest.raises(ValueError, match="HELD_POPULATION_INVALID"):
        regrade.continuation(config, DAY, old)


def test_fabricated_zero_price_raw_is_not_eligible(tmp_path):
    root, previous, source, config, old = held_fixture(
        tmp_path, raw=raw_bytes(zero_close_with_volume=True)
    )
    with pytest.raises(ValueError):
        regrade.continuation(config, DAY, old)


def test_incomplete_grid_is_not_eligible(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path, raw=raw_bytes(drop_index=10))
    with pytest.raises(ValueError, match="GRID_INCOMPLETE"):
        regrade.continuation(config, DAY, old)


def test_fully_traded_raw_declared_r3_is_not_eligible(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path, raw=raw_bytes(zero_index=None))
    with pytest.raises(ValueError, match="NO_TRADE_ABSENT"):
        regrade.continuation(config, DAY, old)


def test_daily_volume_mismatch_is_not_eligible(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    reference = json.loads((previous / "daily-reference.json").read_bytes())
    reference["securities"][CODE]["volume"] = "9600"
    cycle.atomic(previous / "daily-reference.json", reference)
    (source / "daily-reference.json").write_bytes((previous / "daily-reference.json").read_bytes())
    with pytest.raises(ValueError, match="DAILY_VOLUME_DIFFERS"):
        regrade.continuation(config, DAY, old)


def test_changed_capture_bytes_fail_closed(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    p = previous / "capture-5m/shard-000/raw" / (CODE + ".json.gz")
    p.write_bytes(gzip.compress(raw_bytes(zero_index=1), mtime=0))
    with pytest.raises(ValueError, match="CAPTURE_CHANGED"):
        regrade.continuation(config, DAY, old)


def test_main_regrades_frozen_captures_without_recollection(monkeypatch, tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    cycle.write(root / "DATES.json", {"kind": cycle.KIND, "dates": {str(DAY): old}})
    second = root / (str(DAY) + "-20261020T131500000000Z")
    second.mkdir()
    cycle.write(second / "registration.json", {"market_date": str(DAY)})
    regraded = root / "regraded-native"
    regraded.mkdir()
    cycle.write(regraded / "manifest.json", {"research_ready": True, "grades": {"R0": 2}})
    calls = []

    def fake_provider(cfg, run, label, action, *arguments, timeout=3600):
        calls.append((label, action, arguments))
        return {"package_path": str(regraded)}

    finished = {}

    def fake_finish(cfg, run, day, value, market, events, native_root=None, seed=None):
        finished.update(value=value, native_root=native_root, run=run)
        return {**value, "status": "QUALIFIED_1D_NATIVE5M"}

    cycle.write(root / "config.json", config)
    monkeypatch.setattr(cycle, "load", lambda p: dict(config))
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(cycle, "provider", fake_provider)
    monkeypatch.setattr(cycle, "finish_native", fake_finish)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cycle",
            "--config",
            str(root / "config.json"),
            "--day",
            str(DAY),
            "--recovery",
            "--no-trade-regrade",
        ],
    )
    assert cycle.main() == 0
    assert len(calls) == 1
    label, action, arguments = calls[0]
    assert action == "native" and label == "native-regrade"
    assert str(previous / "daily-reference.json") in arguments
    assert str(previous / "capture-5m") in arguments
    assert finished["native_root"] == regraded
    assert finished["value"]["collector_revision"] == cycle.COLLECTOR
    assert (
        finished["value"]["no_trade_regrade"]["original_native_manifest_sha256"]
        == (old["native_manifest_sha256"])
    )
    run = finished["run"]
    evidence = json.loads((run / "no-trade-regrade-continuation.json").read_bytes())
    assert evidence["contract"] == regrade.CONTRACT
    assert evidence["full_daily_reacquisition"] is False
    assert evidence["original_native_manifest_sha256"] == old["native_manifest_sha256"]
    assert evidence["continuation_collector_revision"] == cycle.COLLECTOR
    assert (run / "core-receipt.json").is_file()
    assert json.loads((root / "DATES.json").read_bytes())["dates"][str(DAY)] == old
    assert (previous / "receipt.json").read_bytes() == cycle.canonical(old) + b"\n"


def test_main_without_recovery_flag_keeps_held_disposition(monkeypatch, tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    cycle.write(root / "DATES.json", {"kind": cycle.KIND, "dates": {str(DAY): old}})
    cycle.write(root / "config.json", config)
    monkeypatch.setattr(cycle, "load", lambda p: dict(config))
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["cycle", "--config", str(root / "config.json"), "--day", str(DAY), "--no-trade-regrade"],
    )
    with pytest.raises(ValueError, match="NOT_ELIGIBLE"):
        cycle.main()
    assert json.loads((root / "DATES.json").read_bytes())["dates"][str(DAY)] == old


def test_failed_regrade_never_downgrades_indexed_held_entry(monkeypatch, tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    cycle.write(root / "DATES.json", {"kind": cycle.KIND, "dates": {str(DAY): old}})
    regraded = root / "regraded-native"
    regraded.mkdir()
    cycle.write(regraded / "manifest.json", {"research_ready": False})
    cycle.write(root / "config.json", config)
    monkeypatch.setattr(cycle, "load", lambda p: dict(config))
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(
        cycle,
        "provider",
        lambda cfg, run, label, action, *a, timeout=3600: {"package_path": str(regraded)},
    )
    monkeypatch.setattr(cycle, "finish_native", lambda *a, **kw: pytest.fail("must not finish"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cycle",
            "--config",
            str(root / "config.json"),
            "--day",
            str(DAY),
            "--recovery",
            "--no-trade-regrade",
        ],
    )
    with pytest.raises(ValueError, match="STILL_NOT_READY"):
        cycle.main()
    assert json.loads((root / "DATES.json").read_bytes())["dates"][str(DAY)] == old
    runs = [p for p in root.glob(str(DAY) + "-*") if (p / "failure.json").exists()]
    assert len(runs) == 1


def test_indexed_entry_from_prior_quality_continuation_is_eligible(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    continuation_run = root / (str(DAY) + "-20261020T131500000000Z")
    continuation_run.mkdir()
    indexed = {
        **old,
        "auction_recovery": {"status": "HELD", "reason": "ValueError:AUCTION_NOT_ELIGIBLE"},
        "delivery_timing": "LATE_RECOVERY_NOT_ON_TIME_SUCCESS",
    }
    cycle.write(continuation_run / "receipt.json", indexed)
    value, market, events, captures, reference = regrade.continuation(config, DAY, indexed)
    assert value == indexed
    assert captures == previous / "capture-5m"


def test_stale_index_against_latest_receipt_fails_closed(tmp_path):
    root, previous, source, config, old = held_fixture(tmp_path)
    continuation_run = root / (str(DAY) + "-20261020T131500000000Z")
    continuation_run.mkdir()
    cycle.write(
        continuation_run / "receipt.json",
        {**old, "delivery_timing": "LATE_RECOVERY_NOT_ON_TIME_SUCCESS"},
    )
    with pytest.raises(ValueError, match="RECEIPT_CHANGED"):
        regrade.continuation(config, DAY, old)
