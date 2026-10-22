import json
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle
from quanthus.pipelines.securities_warehouse import daily_base24_provider as worker


@pytest.mark.parametrize("errors", [["0"], ["temporary", "0"], ["temporary", "temporary"]])
def test_initial_login_retries_once_preserving_original_responses(monkeypatch, tmp_path, errors):
    responses = list(errors)
    observations, sleeps = [], []
    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: "2026-10-22T13:20:00+00:00"
    evidence.capture = lambda **kw: observations.append(kw)
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    client = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code=responses.pop(0), error_msg="original")
    )
    result = worker.NativeCaptureClient(client, tmp_path).login()
    assert result.error_code == errors[-1]
    assert [json.loads(v["raw"])["error_code"] for v in observations] == errors
    assert all(
        v["request"]["api"] == "login" and v["status"] == e
        for v, e in zip(observations, errors, strict=True)
    )
    assert sleeps == ([1.0] if len(errors) == 2 else [])
    assert not responses


def fixture_repair(tmp_path):
    previous = tmp_path / "2026-10-22-resume"
    previous.mkdir()
    (previous / "native-capture-resume.json").write_text("{}")
    now = datetime.now(timezone.utc)
    (previous / "native-write-budget.json").write_text(
        json.dumps({"UTC": now.isoformat(), "max_seconds": 3600, "resume_seed": "original"})
    )
    shard = previous / "capture-5m/shard-066"
    (shard / "raw").mkdir(parents=True)
    (shard / "receipts").mkdir()
    (shard / "identity.json").write_text("{}")
    (previous / "capture-066.log").write_text("Traceback...\nValueError: native 5m login failed\n")
    (previous / "failure.json").write_text(
        json.dumps({"reason": "DAILY_CYCLE_PROVIDER_FAILED_capture-066"})
    )
    rules = {"max_seconds": 900, "max_failed_shards": 4}
    return previous, shard, rules, now


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "query_failure",
        "source_rows",
        "receipt",
        "summary",
        "failure_rows",
        "unknown_error",
        "too_many",
        "expired",
        "already_repaired",
    ],
)
def test_only_empty_login_failures_repair_within_original_capture_deadline(tmp_path, mutation):
    previous, shard, rules, now = fixture_repair(tmp_path)
    if mutation == "query_failure":
        (previous / "failure.json").write_text(
            json.dumps({"reason": "DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE"})
        )
    elif mutation == "source_rows":
        (shard / "raw/x.json.gz").write_bytes(b"original")
    elif mutation == "receipt":
        (shard / "receipts/x.json").write_text("{}")
    elif mutation == "summary":
        (shard / "summary.json").write_text("{}")
    elif mutation == "failure_rows":
        (shard / "failures").mkdir()
    elif mutation == "unknown_error":
        (previous / "capture-066.log").write_text("ValueError: something else\n")
    elif mutation == "too_many":
        for i in range(67, 71):
            (previous / f"capture-{i:03}.log").write_text("ValueError: native 5m login failed\n")
    elif mutation == "expired":
        (previous / "native-write-budget.json").write_text(
            json.dumps(
                {
                    "UTC": (now - timedelta(hours=2)).isoformat(),
                    "max_seconds": 3600,
                    "resume_seed": "original",
                }
            )
        )
    elif mutation == "already_repaired":
        (previous / "native-login-repair.json").write_text("{}")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if mutation:
        with pytest.raises(ValueError):
            cycle.login_repair_plan(previous, rules)
    else:
        plan = cycle.login_repair_plan(previous, rules)
        assert plan["failed_shards"] == [66] and plan["max_seconds"] == 900
        assert datetime.fromisoformat(plan["original_capture_deadline"]) == now + timedelta(
            seconds=3600
        )
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_controller_cancels_queued_shards_when_a_worker_fails(monkeypatch, tmp_path):
    from concurrent.futures import Future

    futures = []

    class Pool:
        def __init__(self, max_workers):
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, fn, index):
            value = Future()
            if index == 0:
                value.set_exception(RuntimeError("worker failed"))
            futures.append(value)
            return value

    def provider(config, run, label, action, *args, **kw):
        assert action == "reference"
        (run / "daily-reference.json").write_text("{}")
        return {"population": 500}

    monkeypatch.setattr(cycle.concurrent.futures, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(cycle, "provider", provider)
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    for name in ["market", "events"]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "release.json").write_text("{}")
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(RuntimeError, match="worker failed"):
        cycle.native(
            {"native_workers": 2},
            run,
            datetime.now().date(),
            tmp_path / "market",
            tmp_path / "events",
        )
    assert len(futures) == 10 and all(f.cancelled() for f in futures[1:])


def test_login_only_continuation_reuses_exact_closure_without_an_extra_hour(monkeypatch, tmp_path):
    previous, shard, rules, now = fixture_repair(tmp_path)
    core = tmp_path / "2026-10-22-original/core"
    core.mkdir(parents=True)
    sources = [tmp_path / "sources/2026-10-22" / label for label in ["market", "events"]]
    for label, path in zip(["market", "events"], sources, strict=True):
        path.mkdir(parents=True)
        (path / "release.json").write_text("{}")
        (core.parent / (label + "-source.json")).write_text(json.dumps({"release_dir": str(path)}))
    (previous / "daily-reference.json").write_text("{}")
    source_ids = {
        "market_daily_core": cycle.file_sha(sources[0] / "release.json"),
        "calendar_daily_events": cycle.file_sha(sources[1] / "release.json"),
    }
    old = {
        "market_date": "2026-10-22",
        "status": "CORE_READY_5M_FAILED",
        "base_manifest_sha256": "base",
        "config_sha256": "policy",
        "core": str(core),
        "core_manifest_sha256": "core",
        "source_manifest_sha256": source_ids,
    }
    (previous / "core-receipt.json").write_text(json.dumps(old))
    budget = json.loads((previous / "native-write-budget.json").read_text())
    budget.update(
        reference_sha256=cycle.file_sha(previous / "daily-reference.json"),
        market_manifest_sha256=source_ids["market_daily_core"],
        events_manifest_sha256=source_ids["calendar_daily_events"],
    )
    (previous / "native-write-budget.json").write_text(json.dumps(budget))
    config = {
        "root": str(tmp_path),
        "recovery_mode": True,
        "base_manifest_sha256": "base",
        "release_config_sha256": "policy",
    }
    monkeypatch.setattr(
        cycle, "verify_daily_domain_increment", lambda p: SimpleNamespace(manifest_sha256="core")
    )
    assert cycle.capture_continuation(config, now.date(), old) == (
        old,
        *sources,
        previous / "capture-5m",
    )
    assert config["native_capture_seconds"] == 900
    assert datetime.fromisoformat(config["native_capture_deadline"]) == now + timedelta(
        seconds=3600
    )
    (tmp_path / "2026-10-22-repair").mkdir()
    (tmp_path / "2026-10-22-repair/native-login-repair.json").write_text("{}")
    with pytest.raises(ValueError):
        cycle.capture_continuation(config, now.date(), old)
