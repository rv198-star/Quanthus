"""Qualified 1m output must be the selected whole-security candidate, not old rows."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quanthus.providers import minute_tdx
from quanthus.providers.minute_source_selection import MinuteCandidate, qualify_candidate
from quanthus.providers.tencent_m1_normalize import expected_times

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

DAY = "2026-09-24"
SYMBOL = "000001"
DAILY = {"volume": 24_000, "close": 10, "tradestatus": "1"}


def candidate_and_frame(*, traded: bool = True):
    rows = (
        tuple(
            {
                "stock_code": SYMBOL,
                "ts": f"{DAY} {stamp[-4:-2]}:{stamp[-2:]}:00",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 100.0,
                "amount": None,
            }
            for stamp in expected_times(DAY)
        )
        if traded
        else ()
    )
    candidate = MinuteCandidate(
        provider="tencent-mkline-validated",
        raw_sha256="a" * 64,
        raw_ref="synthetic-source-only.json.gz",
        rows=rows,
        auction_provenance="tencent-m1-normalize-v1",
        amount_status="source_absent_null_not_estimated",
    )
    frame = pd.DataFrame(
        rows,
        columns=("stock_code", "ts", "open", "high", "low", "close", "volume", "amount"),
    )
    frame["ts"] = pd.to_datetime(frame["ts"])
    frame["amount"] = pd.Series([pd.NA] * len(frame), dtype="Float64")
    return candidate, frame


def qualified_hash(candidate, *, daily=DAILY):
    return qualify_candidate(
        candidate,
        symbol=SYMBOL,
        market_date=DAY,
        daily_volume=daily["volume"],
        daily_close=daily["close"],
        traded=daily["tradestatus"] == "1",
    )


def write(home, candidate, frame, *, daily=DAILY):
    return minute_tdx._write_qualified_one_minute(
        home,
        SYMBOL,
        frame,
        candidate=candidate,
        market_date=DAY,
        daily=daily,
        expected_semantic_sha256=qualified_hash(candidate, daily=daily),
    )


def invoke(home, *, symbols=None):
    return minute_tdx.collect(
        home=home,
        layer="research",
        frequency="1m",
        mode="incr",
        symbols=symbols or [SYMBOL],
        max_pages=1,
        sleep_seconds=0,
        market_date=DAY,
        tencent_daily_path=home.parent / "sealed-daily.parquet",
        tencent_status_path=home.parent / "sealed-status.parquet",
    )


def stub_source(monkeypatch, frame):
    calls = []

    def fetch(symbol, *_args):
        calls.append(symbol)
        return frame.copy(), {
            "ok": True,
            "provider": "tencent-mkline-validated",
            "source_raw_sha256": "a" * 64,
            "source_raw_path": "synthetic-source-only.json.gz",
        }

    monkeypatch.setattr(
        minute_tdx, "_tencent_daily_evidence", lambda **kw: {code: DAILY for code in kw["symbols"]}
    )
    monkeypatch.setattr(minute_tdx, "_tencent_one_minute_bars", fetch)
    monkeypatch.setattr(minute_tdx.time, "sleep", lambda _seconds: None)
    return calls


@pytest.mark.parametrize("old_kind", ["same_date", "earlier_date", "partial", "empty"])
def test_reused_home_preserves_old_rows_and_receipts_before_requests(
    tmp_path,
    monkeypatch,
    old_kind,
):
    home = tmp_path / "attempt"
    home.mkdir()
    _, old = candidate_and_frame()
    old.loc[0, "volume"] = 99
    old.loc[0, "close"] = 9.99
    if old_kind == "earlier_date":
        old["ts"] -= pd.Timedelta(days=1)
    elif old_kind == "partial":
        old = old.iloc[:3]
    elif old_kind == "empty":
        old = old.iloc[:0]
    target = home / f"{SYMBOL}_1m.parquet"
    old.to_parquet(target, index=False)
    (home / "status.json").write_text('{"state":"prior"}')
    (home / "checkpoint.json").write_text('{"symbols":"prior"}')
    before = {path.name: path.read_bytes() for path in home.iterdir()}
    _, new = candidate_and_frame()
    calls = stub_source(monkeypatch, new)

    with pytest.raises(ValueError, match="fresh, empty attempt home"):
        invoke(home)

    assert calls == []
    assert {path.name: path.read_bytes() for path in home.iterdir()} == before


@pytest.mark.parametrize("evidence", ["status.json", "checkpoint.json", "provider_raw", "log.txt"])
def test_any_prior_attempt_content_requires_new_home(tmp_path, monkeypatch, evidence):
    home = tmp_path / "attempt"
    home.mkdir()
    if evidence == "provider_raw":
        (home / evidence).mkdir()
    else:
        (home / evidence).write_text("prior attempt")
    _, frame = candidate_and_frame()
    calls = stub_source(monkeypatch, frame)
    with pytest.raises(ValueError, match="fresh, empty attempt home"):
        invoke(home)
    assert calls == []


def test_duplicate_symbols_fail_before_creating_attempt(tmp_path, monkeypatch):
    _, frame = candidate_and_frame()
    calls = stub_source(monkeypatch, frame)
    home = tmp_path / "attempt"
    with pytest.raises(ValueError, match="unique symbols"):
        invoke(home, symbols=[SYMBOL, SYMBOL])
    assert calls == []
    assert not home.exists()


def test_cli_rejects_directory_symlink_before_resolving_it(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "destination"
    destination.mkdir()
    home = tmp_path / "linked-home"
    home.symlink_to(destination, target_is_directory=True)
    _, frame = candidate_and_frame()
    calls = stub_source(monkeypatch, frame)
    assert (
        minute_tdx.main(
            [
                "--layer",
                "research",
                "--freq",
                "1m",
                "--mode",
                "incr",
                "--symbols",
                SYMBOL,
                "--home",
                str(home),
                "--market-date",
                DAY,
                "--tencent-daily-path",
                str(tmp_path / "daily.parquet"),
                "--tencent-status-path",
                str(tmp_path / "status.parquet"),
            ]
        )
        == 2
    )
    assert "fresh, empty attempt home" in capsys.readouterr().err
    assert calls == []
    assert list(destination.iterdir()) == []


def test_fresh_collect_records_hash_of_the_actual_materialization(tmp_path, monkeypatch):
    candidate, frame = candidate_and_frame()
    calls = stub_source(monkeypatch, frame)

    def forbidden_merge(*_args):
        raise AssertionError("qualified 1m must never call the legacy merging writer")

    monkeypatch.setattr(minute_tdx, "_merge_write", forbidden_merge)
    home = tmp_path / "attempt"
    assert invoke(home) == 0
    assert calls == [SYMBOL]
    receipt = json.loads((home / "checkpoint.json").read_text())["symbols"][SYMBOL]
    assert receipt["status"] == "done"
    assert receipt["source_semantic_sha256"] == qualified_hash(candidate)
    assert receipt["materialized_semantic_sha256"] == receipt["source_semantic_sha256"]
    actual = pd.read_parquet(receipt["path"])
    assert len(actual) == receipt["rows_file"] == 240
    assert actual["volume"].sum() == DAILY["volume"]
    assert actual["amount"].isna().all()


@pytest.mark.parametrize("traded", [True, False])
def test_writer_never_retains_or_overwrites_an_existing_target(tmp_path, traded):
    _, old = candidate_and_frame()
    old["ts"] -= pd.Timedelta(days=1)
    target = tmp_path / f"{SYMBOL}_1m.parquet"
    old.to_parquet(target, index=False)
    before = target.read_bytes()
    candidate, frame = candidate_and_frame(traded=traded)
    daily = DAILY if traded else {"volume": 0, "close": 10, "tradestatus": "0"}
    with pytest.raises(FileExistsError, match="target already exists"):
        write(tmp_path, candidate, frame, daily=daily)
    assert target.read_bytes() == before


@pytest.mark.parametrize("change", ["semantic", "volume", "close", "grid", "wrong_date", "empty"])
def test_staged_readback_must_reproduce_exact_whole_candidate(tmp_path, change):
    candidate, frame = candidate_and_frame()
    if change == "semantic":
        frame.loc[0, "high"] = 10.01  # Still daily-qualified; different minute semantics.
    elif change == "volume":
        frame.loc[0, "volume"] = 101
    elif change == "close":
        frame.loc[239, ["high", "close"]] = 10.01
    elif change == "grid":
        frame = frame.iloc[:239]
    elif change == "wrong_date":
        frame["ts"] -= pd.Timedelta(days=1)
    else:
        frame = frame.iloc[:0]
    with pytest.raises(ValueError):
        write(tmp_path, candidate, frame)
    assert list(tmp_path.iterdir()) == []


def test_readback_corruption_is_not_published(tmp_path, monkeypatch):
    candidate, frame = candidate_and_frame()
    damaged = frame.copy()
    damaged.loc[0, "high"] = 10.01
    monkeypatch.setattr(pd, "read_parquet", lambda *_args, **_kw: damaged)
    with pytest.raises(ValueError, match="differs from selected source semantics"):
        write(tmp_path, candidate, frame)
    assert list(tmp_path.iterdir()) == []


def test_cleanup_error_does_not_mask_original_consistency_failure(tmp_path, monkeypatch, capsys):
    candidate, frame = candidate_and_frame()
    frame.loc[0, "high"] = 10.01

    def failed_cleanup(*_args, **_kwargs):
        raise OSError("synthetic staging cleanup fault")

    monkeypatch.setattr(Path, "unlink", failed_cleanup)
    with pytest.raises(ValueError, match="differs from selected source semantics"):
        write(tmp_path, candidate, frame)
    assert "staging cleanup failed" in capsys.readouterr().err
    assert not (tmp_path / f"{SYMBOL}_1m.parquet").exists()


def test_late_target_collision_never_overwrites_the_other_writer(tmp_path, monkeypatch):
    candidate, frame = candidate_and_frame()
    real_link = os.link
    target = tmp_path / f"{SYMBOL}_1m.parquet"

    def competing_link(source, destination):
        target.write_bytes(b"other writer's immutable evidence")
        real_link(source, destination)

    monkeypatch.setattr(minute_tdx.os, "link", competing_link)
    with pytest.raises(FileExistsError):
        write(tmp_path, candidate, frame)
    assert target.read_bytes() == b"other writer's immutable evidence"
    assert list(tmp_path.iterdir()) == [target]


def test_materialization_failure_stops_further_source_requests_and_records_failure(
    tmp_path,
    monkeypatch,
):
    _, frame = candidate_and_frame()
    calls = stub_source(monkeypatch, frame)

    def broken_write(*_args, **_kwargs):
        raise ValueError("qualified 1m materialization differs from selected source semantics")

    monkeypatch.setattr(minute_tdx, "_write_qualified_one_minute", broken_write)
    home = tmp_path / "attempt"
    with pytest.raises(ValueError, match="materialization differs"):
        invoke(home, symbols=[SYMBOL, "600000"])
    assert calls == [SYMBOL]
    status = json.loads((home / "status.json").read_text())
    checkpoint = json.loads((home / "checkpoint.json").read_text())["symbols"]
    assert status["state"] == "failed"
    assert status["done"] == 0
    assert set(checkpoint) == {SYMBOL}
    assert checkpoint[SYMBOL]["status"] == "error"
    assert not (home / f"{SYMBOL}_1m.parquet").exists()


def test_suspended_security_does_not_leave_a_ghost_file(tmp_path):
    candidate, frame = candidate_and_frame(traded=False)
    daily = {"volume": 0, "close": 10, "tradestatus": "0"}
    count, path, digest = write(tmp_path, candidate, frame, daily=daily)
    assert count == 0
    assert not path.exists()
    assert digest == qualified_hash(candidate, daily=daily)
    assert list(tmp_path.iterdir()) == []


def test_unqualified_legacy_merge_contract_is_not_silently_changed(tmp_path):
    _, old = candidate_and_frame()
    minute_tdx._merge_write(tmp_path, SYMBOL, "1m", old)
    new = old.copy()
    new.loc[0, "volume"] = 101
    new["ts"] += pd.Timedelta(days=1)
    count, path = minute_tdx._merge_write(tmp_path, SYMBOL, "1m", new)
    assert count == 480
    assert len(pd.read_parquet(path)) == 480
