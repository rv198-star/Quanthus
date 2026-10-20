"""The intake seals identities, while the downstream DAG qualifies payloads."""

import fcntl
import json
from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_intake as intake
from quanthus.pipelines.securities_warehouse import daily_base24_job as job
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(job.canonical(value) + b"\n")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    args = dict(
        market_date=date(2026, 10, 20),
        code_revision="a" * 40,
        source_root=tmp_path / "sources",
        native5m=tmp_path / "native5m",
        base=tmp_path / "base",
        release_config=tmp_path / "config.json",
        prior_pointer=tmp_path / "prior.json",
        plan_root=tmp_path / "plans",
        releases={lane: "daily/2026-10-20/" + lane for lane in sorted(job.LANES)},
        mode="t-day",
    )
    for lane, relative in args["releases"].items():
        root = args["source_root"] / relative
        write(
            root / "release.json",
            {
                "market_date": "2026-10-20",
                "release_id": intake.PREFIXES[lane] + "20261020-synthetic-v1",
            },
        )
        (root / "READY").write_text(job.file_sha(root / "release.json") + "\n")
    for key, kind in (("base", intake.BASE_KIND), ("native5m", intake.NATIVE_KIND)):
        root = args[key]
        write(
            root / "manifest.json",
            {
                "kind": kind,
                "market_date": "2026-10-20",
                "interval": "5m",
                "execution_authorized": False,
                "policy_id": "p10-trading-day-complete-daily-v1",
            },
        )
        (root / "READY").write_text(job.file_sha(root / "manifest.json") + "\n")
    for key in ("release_config", "prior_pointer"):
        write(args[key], {"synthetic": key})
    monkeypatch.setattr(intake, "_now", lambda: datetime(2026, 10, 20, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(job, "_now", intake._now)
    return args


def target(args):
    return args["plan_root"] / (str(args["market_date"]) + ".json")


def lock(args):
    p = args["plan_root"]
    return p.with_name("." + p.name + ".publish.lock")


def test_immutable_publication_and_scheduled_consumer(setup):
    args = setup
    before = {p: p.read_bytes() for p in args["source_root"].rglob("*") if p.is_file()}
    receipt = intake.publish(**args)
    p = target(args)
    plan = json.loads(p.read_bytes())
    job.validate_plan_inputs(plan)
    assert job.wait_for_plan(args["plan_root"]) == p
    assert set(plan["lanes"]) == job.LANES
    assert receipt["payload_qualification"] == "REQUIRED_IN_DOWNSTREAM_DAILY_DAG"
    assert not receipt["natural_T_day_acquisition_claim"]
    assert not receipt["execution_authorized"]
    prior = (p.read_bytes(), p.stat().st_mtime_ns)
    again = intake.publish(**args)
    assert again["status"] == "REUSED_FROZEN_INPUT_PLAN"
    assert prior == (p.read_bytes(), p.stat().st_mtime_ns)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    seal = p.with_name(p.name + ".READY")
    assert seal.stat().st_nlink == 1
    assert sorted(x.name for x in args["plan_root"].iterdir()) == [p.name, seal.name]


@pytest.mark.parametrize(
    "which",
    [
        "missing_ready",
        "wrong_ready",
        "wrong_date",
        "wrong_lane",
        "one_minute",
        "native_kind",
        "base_kind",
        "code_revision",
    ],
)
def test_bad_source_rejected_before_any_output(setup, which):
    args = setup
    root = args["source_root"] / args["releases"]["market_daily_core"]
    if which == "missing_ready":
        (root / "READY").unlink()
    elif which == "wrong_ready":
        (root / "READY").write_text("sha256:" + "0" * 64)
    elif which in {"wrong_date", "wrong_lane"}:
        p = root / "release.json"
        value = json.loads(p.read_bytes())
        value["market_date" if which == "wrong_date" else "release_id"] = "2026-10-19"
        write(p, value)
        (root / "READY").write_text(job.file_sha(p))
    elif which == "code_revision":
        args["code_revision"] = "short"
    else:
        root = args["base" if which == "base_kind" else "native5m"]
        p = root / "manifest.json"
        value = json.loads(p.read_bytes())
        value["interval" if which == "one_minute" else "kind"] = "1m"
        write(p, value)
        (root / "READY").write_text(job.file_sha(p))
    with pytest.raises((ValueError, OSError)):
        intake.publish(**args)
    assert not args["plan_root"].exists()
    assert not lock(args).exists()


@pytest.mark.parametrize("bad", ["extra_lane", "missing_lane", "absolute", "parent", "dot"])
def test_exact_source_selection_and_relative_paths(setup, bad):
    args = setup
    if bad == "extra_lane":
        args["releases"]["intraday_1m"] = "fake"
    elif bad == "missing_lane":
        del args["releases"]["market_daily_core"]
    else:
        args["releases"]["market_daily_core"] = {
            "absolute": "/tmp/outside",
            "parent": "../outside",
            "dot": ".",
        }[bad]
    with pytest.raises(ValueError, match="DAILY_INTAKE"):
        intake.publish(**args)
    assert not args["plan_root"].exists()
    assert not lock(args).exists()


@pytest.mark.parametrize(
    "which",
    [
        "inside_source",
        "source_parent",
        "lock_is_source",
        "root_symlink",
        "target_symlink",
        "seal_symlink",
    ],
)
def test_source_path_and_byte_preservation(setup, tmp_path, which):
    args = setup
    if which == "inside_source":
        args["plan_root"] = args["source_root"] / "plans"
    elif which == "source_parent":
        args["plan_root"] = tmp_path
    elif which == "lock_is_source":
        new_source = lock(args)
        args["source_root"].rename(new_source)
        args["source_root"] = new_source
    elif which == "root_symlink":
        args["plan_root"].symlink_to(args["source_root"], target_is_directory=True)
    else:
        args["plan_root"].mkdir()
        p = target(args)
        if which == "seal_symlink":
            p = p.with_name(p.name + ".READY")
        p.symlink_to(args["release_config"])
    before = {p: p.read_bytes() for p in args["source_root"].rglob("*") if p.is_file()}
    with pytest.raises(SourceAssessmentError, match="SOURCE_OUTPUT_OVERLAP|SOURCE_SYMLINK"):
        intake.publish(**args)
    assert before == {p: p.read_bytes() for p in args["source_root"].rglob("*") if p.is_file()}
    if which != "lock_is_source":
        assert not lock(args).exists()


def test_changed_identity_never_overwrites_existing_plan(setup):
    args = setup
    intake.publish(**args)
    before = {p: p.read_bytes() for p in args["plan_root"].iterdir()}
    args["code_revision"] = "b" * 40
    with pytest.raises(ValueError, match="EXISTING_IDENTITY_DIFFERS"):
        intake.publish(**args)
    assert before == {p: p.read_bytes() for p in args["plan_root"].iterdir()}


def test_interrupted_plan_can_only_finish_exact_identity(setup, monkeypatch):
    args = setup
    original = intake._seal

    def fail(*_):
        raise OSError("simulated interruption before seal")

    monkeypatch.setattr(intake, "_seal", fail)
    with pytest.raises(OSError, match="interruption"):
        intake.publish(**args)
    p = target(args)
    stamp = p.stat().st_mtime_ns
    assert not p.with_name(p.name + ".READY").exists()
    args["code_revision"] = "b" * 40
    with pytest.raises(ValueError, match="EXISTING_IDENTITY_DIFFERS"):
        intake.publish(**args)
    args["code_revision"] = "a" * 40
    monkeypatch.setattr(intake, "_seal", original)
    intake.publish(**args)
    assert p.stat().st_mtime_ns == stamp
    assert p.with_name(p.name + ".READY").is_file()


@pytest.mark.parametrize("change", ["controls", "deadline"])
def test_change_after_plan_write_cannot_publish_readiness(setup, monkeypatch, change):
    args = setup
    original = intake._exclusive

    def interrupt(path, raw):
        original(path, raw)
        if change == "controls":
            write(args["prior_pointer"], {"changed": True})
        else:
            monkeypatch.setattr(
                intake, "_now", lambda: datetime(2026, 10, 20, 12, 45, tzinfo=timezone.utc)
            )

    monkeypatch.setattr(intake, "_exclusive", interrupt)
    with pytest.raises((ValueError, TimeoutError)):
        intake.publish(**args)
    assert target(args).exists()
    assert not target(args).with_name(target(args).name + ".READY").exists()


@pytest.mark.parametrize("hour,minute", [(9, 29), (12, 45), (13, 0)])
def test_intake_window_rejected_before_writes(setup, monkeypatch, hour, minute):
    args = setup
    monkeypatch.setattr(
        intake, "_now", lambda: datetime(2026, 10, 20, hour, minute, tzinfo=timezone.utc)
    )
    with pytest.raises((ValueError, TimeoutError)):
        intake.publish(**args)
    assert not args["plan_root"].exists()
    assert not lock(args).exists()


def test_replay_does_not_assert_current_day_acquisition(setup):
    setup["mode"] = "replay"
    result = intake.publish(**setup)
    assert result["mode"] == "replay"
    assert result["natural_T_day_acquisition_claim"] is False


def test_lock_contention_never_creates_partial_plan(setup):
    args = setup
    with lock(args).open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            intake.publish(**args)
    assert not args["plan_root"].exists()


def test_orphan_seal_is_preserved_and_not_used(setup):
    args = setup
    args["plan_root"].mkdir()
    seal = target(args).with_name(target(args).name + ".READY")
    seal.write_text("sha256:" + "f" * 64)
    before = seal.read_bytes()
    with pytest.raises(ValueError, match="EXISTING_IDENTITY_DIFFERS"):
        intake.publish(**args)
    assert seal.read_bytes() == before
    assert not target(args).exists()


def test_atomic_seal_never_overwrites_and_cleans_its_own_tmp(tmp_path):
    seal = tmp_path / "READY"
    seal.write_text("retained")
    with pytest.raises(FileExistsError):
        intake._seal(seal, "sha256:" + "a" * 64)
    assert seal.read_text() == "retained"
    assert list(tmp_path.iterdir()) == [seal]
