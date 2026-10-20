"""Scheduling/admission boundaries of the frozen-input Daily DAG.

Builders are stubbed here to test ordering/failure paths. Real domain/Decimal/
calendar/cold verification remains in test_daily_base24 and the governed trial.
"""

import json
import signal
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_job as job

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    paths = {k: tmp_path / k for k in ("source_root", "native5m", "base")}
    for p in paths.values():
        p.mkdir()
    controls = {}
    for key in ("native5m", "base"):
        p = paths[key] / "manifest.json"
        job._write(p, {"market_date": "2026-10-20"})
        controls[key] = job.file_sha(p)
    for key in ("release_config", "prior_pointer"):
        paths[key] = tmp_path / (key + ".json")
        job._write(paths[key], {"synthetic": key})
        controls[key] = job.file_sha(paths[key])
    lanes = {}
    for lane in sorted(job.LANES):
        parent = paths["source_root"] / lane
        parent.mkdir()
        job._write(parent / "release.json", {"market_date": "2026-10-20"})
        lanes[lane] = {"release_path": lane, "sha256": job.file_sha(parent / "release.json")}
    plan = {
        "kind": job.KIND,
        "market_date": "2026-10-20",
        "code_revision": "a" * 40,
        **{k: str(v) for k, v in paths.items()},
        "control_sha256": controls,
        "lanes": lanes,
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
    }
    plan_path = tmp_path / "plan.json"
    job._write(plan_path, plan)
    args = dict(
        plan_path=plan_path,
        window_path=ROOT / "config/pipelines/base24-daily-window.v1.json",
        output=tmp_path / "job",
        mode="t-day",
    )
    monkeypatch.setattr(job, "_now", lambda: datetime(2026, 10, 20, 10, tzinfo=timezone.utc))
    called = []

    def catalog(**kw):
        called.append("catalog")
        assert kw["product_scope"] == "daily-core-v1"
        kw["output"].mkdir()
        job._write(
            kw["output"] / "catalog.json",
            {
                "lanes": [
                    {
                        "lane": key,
                        "release_path": x["release_path"],
                        "release_manifest_sha256": x["sha256"],
                    }
                    for key, x in lanes.items()
                ]
            },
        )
        return SimpleNamespace(root=kw["output"])

    def core(**kw):
        called.append("core")
        assert kw["catalog_root"] == args["output"] / "catalog"
        kw["output"].mkdir()
        job._write(kw["output"] / "manifest.json", {"synthetic": True})
        return SimpleNamespace(root=kw["output"])

    def composite(**kw):
        called.append("composite")
        assert kw["core"] == args["output"] / "core"
        kw["output"].mkdir()
        job._write(kw["output"] / "manifest.json", {"synthetic": True})

    def verify(root):
        called.append("verify")
        return {
            "manifest_sha256": job.file_sha(root / "manifest.json"),
            "row_counts": {"1d": 1, "5m": 48},
            "source_grades": {"R0": 1},
        }

    monkeypatch.setattr(job, "build_daily_ods_catalog", catalog)
    monkeypatch.setattr(job, "build_daily_domain_increment", core)
    monkeypatch.setattr(job, "build", composite)
    monkeypatch.setattr(job, "verify", verify)
    monkeypatch.setattr(job, "verify_daily_ods_catalog", lambda p: None)
    return args, plan, paths, called


@pytest.mark.parametrize(
    "hour,minute,accepted", [(9, 29, False), (9, 30, True), (12, 59, True), (13, 0, False)]
)
def test_shanghai_1730_to_2100_boundaries(hour, minute, accepted):
    now = datetime(2026, 10, 20, hour, minute, tzinfo=timezone.utc)
    if accepted:
        end = job.delivery_window(date(2026, 10, 20), now, "t-day")
        assert end.astimezone(timezone.utc).hour == 13
    else:
        with pytest.raises(ValueError, match="OUTSIDE_T_DAY_WINDOW"):
            job.delivery_window(date(2026, 10, 20), now, "t-day")


def test_wrong_market_day_or_unknown_clock_cannot_claim_t_day():
    with pytest.raises(ValueError, match="OUTSIDE_T_DAY_WINDOW"):
        job.delivery_window(
            date(2026, 10, 19), datetime(2026, 10, 20, 10, tzinfo=timezone.utc), "t-day"
        )
    with pytest.raises(ValueError, match="CLOCK_OR_MODE"):
        job.delivery_window(date(2026, 10, 20), datetime(2026, 10, 20, 18), "t-day")
    assert job.delivery_window(date(2026, 10, 19), job._now(), "replay") is None


def test_one_shared_core_and_no_1m_or_weekly_prerequisite(setup):
    args, _, _, calls = setup
    receipt = job.run(**args)
    assert calls == ["catalog", "core", "composite", "verify"]
    assert receipt["T_day_build_completed_before_deadline"] is True
    assert receipt["natural_T_day_acquisition_claim"] is False
    assert receipt["execution_authorized"] is False
    assert receipt["weekly_completion_claim"] is False
    assert (args["output"] / "READY").read_text().strip() == job.file_sha(
        args["output"] / "receipt.json"
    )
    before = (args["output"] / "receipt.json").read_bytes()
    assert job.run(**args) == receipt
    assert calls == ["catalog", "core", "composite", "verify", "verify"]
    assert (args["output"] / "receipt.json").read_bytes() == before


@pytest.mark.parametrize("target", ["source_root", "native5m", "base", "release_config"])
def test_output_or_sibling_lock_never_writes_inputs(setup, target):
    args, _, paths, calls = setup
    root = paths[target]
    before = {p: p.read_bytes() for p in paths["source_root"].rglob("*") if p.is_file()}
    args["output"] = root / "bad" if root.is_dir() else root
    with pytest.raises(Exception, match="SOURCE_OUTPUT_OVERLAP"):
        job.run(**args)
    assert calls == []
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not args["output"].with_name("." + args["output"].name + ".lock").exists()


@pytest.mark.parametrize("mutation", ["input", "wrong_date", "extra_control", "one_minute"])
def test_freeze_checks_before_any_output_or_lock(setup, mutation):
    args, plan, paths, calls = setup
    if mutation == "input":
        paths["release_config"].write_text("{}")
    elif mutation == "wrong_date":
        p = paths["native5m"] / "manifest.json"
        p.write_text(json.dumps({"market_date": "2026-10-19"}))
        plan["control_sha256"]["native5m"] = job.file_sha(p)
    elif mutation == "extra_control":
        plan["control_sha256"]["extra"] = "sha256:" + "f" * 64
    else:
        plan["lanes"]["intraday_1m"] = {}
    args["plan_path"].write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        job.run(**args)
    assert calls == []
    assert not args["output"].exists()
    assert not args["output"].with_name(".job.lock").exists()


def test_replay_never_becomes_natural_t_day(setup):
    args, _, _, _ = setup
    args["mode"] = "replay"
    value = job.run(**args)
    assert value["deadline"] is None
    assert not value["T_day_build_completed_before_deadline"]


@pytest.mark.parametrize("failure", ["verify", "deadline", "source_changed", "plan_changed"])
def test_failure_retains_stages_but_never_final_ready(setup, monkeypatch, failure):
    args, _, paths, _ = setup
    original = job.verify

    def fail(root):
        result = original(root)
        if failure == "verify":
            raise ValueError("synthetic rejection")
        if failure == "deadline":
            monkeypatch.setattr(
                job, "_now", lambda: datetime(2026, 10, 20, 13, tzinfo=timezone.utc)
            )
        elif failure == "plan_changed":
            with args["plan_path"].open("a") as stream:
                stream.write("\n")
        else:
            paths["release_config"].write_text("{}")
        return result

    monkeypatch.setattr(job, "verify", fail)
    with pytest.raises((ValueError, TimeoutError)):
        job.run(**args)
    assert not (args["output"] / "READY").exists()
    assert not (args["output"] / "receipt.json").exists()
    assert (args["output"] / "FAILURE.safe.json").exists()
    before = (args["output"] / "FAILURE.safe.json").read_bytes()
    with pytest.raises((ValueError, FileNotFoundError)):
        job.run(**args)
    assert (args["output"] / "FAILURE.safe.json").read_bytes() == before


def test_wrong_selected_revision_fails_before_core(setup, monkeypatch):
    args, _, _, calls = setup
    original = job.build_daily_ods_catalog

    def wrong(**kw):
        result = original(**kw)
        p = result.root / "catalog.json"
        x = json.loads(p.read_text())
        x["lanes"][0]["release_manifest_sha256"] = "sha256:" + "f" * 64
        p.write_text(json.dumps(x))
        return result

    monkeypatch.setattr(job, "build_daily_ods_catalog", wrong)
    with pytest.raises(ValueError, match="SELECTED_SOURCE_DIFFERS"):
        job.run(**args)
    assert calls == ["catalog"]
    assert not (args["output"] / "READY").exists()


def test_sealed_reuse_rejects_damage_without_rebuilding(setup):
    args, _, _, calls = setup
    job.run(**args)
    (args["output"] / "product/manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="EXISTING_IDENTITY_OR_PRODUCT_DIFFERS"):
        job.run(**args)
    assert calls.count("catalog") == calls.count("core") == calls.count("composite") == 1


def test_wall_stop_restores_signal_state():
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError, match="WALL_STOP"), job._wall_stop(0.02):
        time.sleep(0.2)
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    assert signal.getsignal(signal.SIGALRM) == previous


def test_wait_uses_only_today_checksum_sealed_plan(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 20, 10, tzinfo=timezone.utc)]
    monkeypatch.setattr(job, "_now", lambda: clock[0])
    (tmp_path / "2026-10-19.json").write_text("yesterday")
    plan = tmp_path / "2026-10-20.json"
    plan.write_text("{}")
    polls = []

    def arrive(seconds):
        polls.append(seconds)
        (tmp_path / "2026-10-20.json.READY").write_text(job.file_sha(plan))

    monkeypatch.setattr(job.time, "sleep", arrive)
    assert job.wait_for_plan(tmp_path) == plan
    assert polls == [30]


def test_missing_sealed_inputs_stop_at_2045_without_output(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "_now", lambda: datetime(2026, 10, 20, 12, 45, tzinfo=timezone.utc))
    with pytest.raises(TimeoutError, match="SEALED_INPUT_DEADLINE"):
        job.wait_for_plan(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bad_intake_seal_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "_now", lambda: datetime(2026, 10, 20, 10, tzinfo=timezone.utc))
    (tmp_path / "2026-10-20.json").write_text("{}")
    (tmp_path / "2026-10-20.json.READY").write_text("sha256:" + "f" * 64)
    with pytest.raises(ValueError, match="INTAKE_PLAN_SEAL_DIFFERS"):
        job.wait_for_plan(tmp_path)
