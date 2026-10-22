from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from quanthus.pipelines.securities_warehouse.native_capture_resume import seed_capture


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def seed(tmp_path):
    source = tmp_path / "capture"
    shard = source / "shard-000"
    ref = {
        "population": 1,
        "market_date": "2026-10-22",
        "source_ods_manifest_sha256": "sha256:" + "a" * 64,
        "securities": {"000001": {}},
        "reference_digest": "reference",
    }
    identity = {
        "schema_version": 1,
        "kind": "p10_baostock_native_m5_raw_capture_shard",
        "market_date": ref["market_date"],
        "market_ods_manifest_sha256": ref["source_ods_manifest_sha256"],
        "shard_index": 0,
        "selected_symbols": ["000001"],
        "provider": "baostock-native-5m",
        "frequency": "5",
        "adjustflag": "3",
        "formal_warehouse_ready": False,
    }
    save(shard / "identity.json", identity)
    raw = b'{"rows":[]}'
    (shard / "raw").mkdir()
    (shard / "raw/000001.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    save(
        shard / "receipts/000001.json",
        {
            "stock_code": "000001",
            "market_date": ref["market_date"],
            "source_market_ods_sha256": ref["source_ods_manifest_sha256"],
            "provider": "baostock-native-5m",
            "status": "raw_saved",
            "captured_at": "2026-10-22T12:00:00+00:00",
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    save(shard / "summary.json", {**identity, "saved": 1, "errors": 0, "published": False})
    save(shard / "original-responses/evidence.json", {"original": True})
    return source, ref, tmp_path / "out"


def test_independent_seed_retains_exact_bytes_times_and_skips_completed_requests(seed):
    source, ref, out = seed
    before = {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    result = seed_capture(source, out, ref)
    assert result["complete_shards"] == [0]
    assert result["reused_securities"] == 1
    assert result["remaining_securities"] == 0
    assert before == {
        p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()
    }
    assert before == {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    assert all((source / name).stat().st_ino != (out / name).stat().st_ino for name in before)


@pytest.mark.parametrize(
    "mutation",
    [
        "raw",
        "date",
        "source",
        "error",
        "missing_receipt",
        "unknown_symbol",
        "link",
        "extra",
        "existing_output",
        "overlap",
    ],
)
def test_invalid_seed_rejected_before_output_write(seed, mutation):
    source, ref, out = seed
    shard = source / "shard-000"
    if mutation == "raw":
        (shard / "raw/000001.json.gz").write_bytes(gzip.compress(b"changed"))
    elif mutation in {"date", "source"}:
        p = shard / "identity.json"
        v = json.loads(p.read_bytes())
        v["market_date" if mutation == "date" else "market_ods_manifest_sha256"] = "changed"
        save(p, v)
    elif mutation == "error":
        p = shard / "summary.json"
        v = json.loads(p.read_bytes())
        v["errors"] = 1
        save(p, v)
    elif mutation == "missing_receipt":
        (shard / "receipts/000001.json").unlink()
    elif mutation == "unknown_symbol":
        save(shard / "receipts/999999.json", {})
    elif mutation == "link":
        (shard / "other").symlink_to(shard / "identity.json")
    elif mutation == "extra":
        save(source / "extra.json", {})
    elif mutation == "existing_output":
        out.mkdir()
        save(out / "original.json", {"retained": True})
    else:
        out = source / "nested"
    before = {
        str(p): p.read_bytes() for p in source.rglob("*") if p.is_file() and not p.is_symlink()
    }
    with pytest.raises((ValueError, OSError)):
        seed_capture(source, out, ref)
    assert mutation == "existing_output" or not out.exists()
    assert before == {
        str(p): p.read_bytes() for p in source.rglob("*") if p.is_file() and not p.is_symlink()
    }


def test_paired_partial_shard_reused_but_not_skipped(seed):
    source, ref, out = seed
    (source / "shard-000/summary.json").unlink()
    result = seed_capture(source, out, ref)
    assert result["complete_shards"] == []
    assert result["reused_securities"] == 1


def test_controller_requests_only_missing_shards(monkeypatch, tmp_path):
    from datetime import date

    from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle

    source = tmp_path / "old/capture-5m"
    source.mkdir(parents=True)
    (source.parent / "daily-reference.json").write_text("{}")
    run = tmp_path / "run"
    run.mkdir()
    calls = []
    save(tmp_path / "market/release.json", {})
    save(tmp_path / "events/release.json", {})

    def provider(config, run, label, action, *args, **kwargs):
        calls.append((action, args))
        if action == "seed":
            return {
                "population": 100,
                "complete_shards": [0],
                "reused_securities": 50,
                "remaining_securities": 50,
                "seed_inventory_sha256": "seed",
            }
        if action == "capture":
            return {"errors": 0}
        return {"package_path": "sealed"}

    monkeypatch.setattr(cycle, "provider", provider)
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    assert cycle.native(
        {"native_workers": 2},
        run,
        date(2026, 10, 22),
        tmp_path / "market",
        tmp_path / "events",
        source,
    ) == {"package_path": "sealed"}
    captures = [args for action, args in calls if action == "capture"]
    assert len(captures) == 1 and captures[0][-1] == "1"
    assert [action for action, _ in calls].count("reference") == 0
    assert [action for action, _ in calls].count("native") == 1


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "normal_mode",
        "date",
        "base",
        "policy",
        "source",
        "reference",
        "repeat",
        "failure",
        "core",
        "ambiguous",
    ],
)
def test_capture_recovery_eligibility_is_read_only_and_bounded(monkeypatch, tmp_path, mutation):
    from datetime import date
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle

    day = date(2026, 10, 22)
    core = tmp_path / (str(day) + "-core") / "core"
    core.mkdir(parents=True)
    failed = tmp_path / (str(day) + "-failed")
    failed.mkdir()
    capture = failed / "capture-5m"
    capture.mkdir()
    market, events = [tmp_path / "daily" / str(day) / x for x in ["market", "events"]]
    for p in [market, events]:
        save(p / "release.json", {})
    for label, path in [("market", market), ("events", events)]:
        save(core.parent / (label + "-source.json"), {"release_dir": str(path)})
    (failed / "daily-reference.json").write_text("{}")
    expected = {
        "market_daily_core": cycle.file_sha(market / "release.json"),
        "calendar_daily_events": cycle.file_sha(events / "release.json"),
    }
    old = {
        "market_date": str(day),
        "status": "CORE_READY_5M_FAILED",
        "core": str(core),
        "core_manifest_sha256": "core",
        "base_manifest_sha256": "base",
        "config_sha256": "policy",
        "source_manifest_sha256": expected,
    }
    config = {
        "root": str(tmp_path),
        "recovery_mode": True,
        "base_manifest_sha256": "base",
        "release_config_sha256": "policy",
    }
    save(
        failed / "native-write-budget.json",
        {
            "reference_sha256": cycle.file_sha(failed / "daily-reference.json"),
            "market_manifest_sha256": expected["market_daily_core"],
            "events_manifest_sha256": expected["calendar_daily_events"],
        },
    )
    save(failed / "failure.json", {"reason": "DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE"})
    save(failed / "core-receipt.json", old)
    if mutation == "normal_mode":
        config["recovery_mode"] = False
    elif mutation == "date":
        old["market_date"] = "2026-10-21"
    elif mutation == "base":
        config["base_manifest_sha256"] = "different"
    elif mutation == "policy":
        config["release_config_sha256"] = "different"
    elif mutation == "source":
        save(market / "release.json", {"changed": True})
    elif mutation == "reference":
        (failed / "daily-reference.json").write_text("changed")
    elif mutation == "repeat":
        save(tmp_path / (str(day) + "-resumed") / "native-capture-resume.json", {})
    elif mutation == "failure":
        save(failed / "failure.json", {"reason": "unrelated"})
    elif mutation == "ambiguous":
        other = tmp_path / (str(day) + "-other")
        save(other / "core-receipt.json", old)
        save(other / "failure.json", {"reason": "DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE"})
    monkeypatch.setattr(
        cycle,
        "verify_daily_domain_increment",
        lambda p: SimpleNamespace(manifest_sha256="changed" if mutation == "core" else "core"),
    )
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if mutation:
        with pytest.raises(ValueError):
            cycle.capture_continuation(config, day, old)
    else:
        assert cycle.capture_continuation(config, day, old) == (old, market, events, capture)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_late_verified_data_is_explicit_in_terminal_event():
    from datetime import date

    from quanthus.pipelines.securities_warehouse.container_jobs import event

    value = event(
        "recovery",
        date(2026, 10, 22),
        "recovery-test",
        "a" * 40,
        {
            "status": "QUALIFIED_1D_NATIVE5M",
            "product_manifest_sha256": "sha256:" + "b" * 64,
            "delivery_timing": "LATE_RECOVERY_NOT_ON_TIME_SUCCESS",
        },
    )
    assert value["status"] == "complete"
    assert value["reason"] == "LATE_RECOVERY_QUALIFIED_1D_NATIVE5M"


def test_recovery_main_never_recollects_daily_or_rebuilds_core(monkeypatch, tmp_path, capsys):
    import sys

    from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle

    day = "2026-10-22"
    failed = tmp_path / (day + "-failed")
    failed.mkdir()
    seed = failed / "capture-5m"
    seed.mkdir()
    (failed / "daily-reference.json").write_text("{}")
    old = {"status": "CORE_READY_5M_FAILED"}
    save(tmp_path / "DATES.json", {"dates": {day: old}})
    save(failed / "failure.json", {"reason": "DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE"})
    save(failed / "registration.json", {})
    save(tmp_path / (day + "-other") / "registration.json", {})
    cfg = {"root": str(tmp_path), "pipeline_revision": "b" * 40}
    value = {
        "core": "original-core",
        "core_manifest_sha256": "core",
        "source_manifest_sha256": {"market": "market"},
    }
    monkeypatch.setattr(cycle, "load", lambda p: cfg)
    monkeypatch.setattr(cycle, "time_gate", lambda *a: None)
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(cycle, "collect", lambda *a: pytest.fail("no market/events recollection"))
    monkeypatch.setattr(
        cycle, "build_daily_domain_increment", lambda **kw: pytest.fail("no core rebuild")
    )
    monkeypatch.setattr(
        cycle, "reference_continuation", lambda *a: pytest.fail("must use capture recovery")
    )
    monkeypatch.setattr(
        cycle,
        "capture_continuation",
        lambda *a: (dict(value), tmp_path / "market", tmp_path / "events", seed),
    )

    def finish(*args, **kwargs):
        assert kwargs["seed"] == seed
        assert args[3]["delivery_timing"] == "LATE_RECOVERY_NOT_ON_TIME_SUCCESS"
        return {"status": "QUALIFIED_1D_NATIVE5M"}

    monkeypatch.setattr(cycle, "finish_native", finish)
    monkeypatch.setattr(
        sys, "argv", ["cycle", "--config", str(tmp_path / "DATES.json"), "--day", day, "--recovery"]
    )
    assert cycle.main() == 0
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "QUALIFIED_1D_NATIVE5M"
    assert len(list(tmp_path.glob(day + "-*/native-capture-resume.json"))) == 1
