"""P10 end-of-pipeline boundaries and host notifications without Docker/network."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus import p10_container_control as host
from quanthus import p10_operational_mail as mail
from quanthus.pipelines.securities_warehouse import container_code as code
from quanthus.pipelines.securities_warehouse import container_jobs as jobs
from tests.unit.test_base24_weekly_package import product

DAY = date(2026, 10, 21)
SHA = "a" * 40


@pytest.fixture
def source_bundle(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "src").mkdir()
    (repo / "config").mkdir()
    (repo / "src/module.py").write_text("value = 1\n")
    (repo / "config/schema.json").write_text("{}\n")
    subprocess.run(["git", "add", "src", "config"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "private.env").write_text("DO_NOT_EXPORT=private\n")
    root = tmp_path / "bundle"
    code.bundle(repo, revision, root)
    return repo, root, revision


def test_bundled_code_survives_two_moves_without_git_checkout(source_bundle, tmp_path):
    repo, root, revision = source_bundle
    shutil.rmtree(repo)
    for index in range(2):
        moved = tmp_path / f"move-{index}"
        shutil.copytree(root, moved)
        shutil.rmtree(root)
        assert len(code.verify_bundle(moved, revision)["files"]) == 2
        assert not (moved / "private.env").exists()
        root = moved


@pytest.mark.parametrize("mutation", ["extra", "missing", "bytes", "symlink", "revision"])
def test_source_bundle_fail_closed(source_bundle, mutation, tmp_path):
    _, root, revision = source_bundle
    member = root / "src/module.py"
    if mutation == "extra":
        (root / "unexpected.txt").write_text("extra")
    elif mutation == "missing":
        member.unlink()
    elif mutation == "bytes":
        member.write_text("value = 2\n")
    elif mutation == "symlink":
        member.unlink()
        member.symlink_to(tmp_path / "not-an-input")
    else:
        revision = SHA
    with pytest.raises((ValueError, OSError)):
        code.verify_bundle(root, revision)


def safe_event(status="QUALIFIED_1D_NATIVE5M"):
    return jobs.event(
        "daily",
        DAY,
        "daily-test",
        SHA,
        {
            "status": status,
            "market_date": str(DAY),
            "product_manifest_sha256": "sha256:" + "b" * 64,
        },
    )


@pytest.mark.parametrize("status", ["ready", "source_only", "CORE_READY", "NOT_READY"])
def test_only_ods_dwd_verified_terminal_status_can_complete(status):
    with pytest.raises(ValueError, match="TERMINAL_STATUS"):
        jobs.event("daily", DAY, "daily-test", SHA, {"status": status})


def test_scoped_weekly_or_held_minutes_never_claim_full_completion():
    value = safe_event("CORE_READY_5M_HELD_SOURCE_QUALITY")
    assert value["status"] == "partial"
    with pytest.raises(ValueError, match="MANIFEST"):
        jobs.event("daily", DAY, "daily-test", SHA, {"status": "QUALIFIED_1D_NATIVE5M"})


@pytest.mark.parametrize("recovery", [False, True])
def test_daily_invokes_full_existing_cycle_not_source_only(tmp_path, monkeypatch, recovery):
    def fake_run(command, **kwargs):
        assert command[2] == "quanthus.pipelines.securities_warehouse.daily_base24_cycle"
        assert ("--recovery" in command) == recovery
        assert "--native-only" not in command
        kwargs["stdout"].write(json.dumps({"result": {"status": "QUALIFIED_1D_NATIVE5M"}}).encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    assert jobs.daily(tmp_path / "config", DAY, recovery)["status"] == "QUALIFIED_1D_NATIVE5M"


def private_mail(tmp_path):
    path = tmp_path / "private-mail.json"
    path.write_text(
        json.dumps(
            {
                "api_key": "synthetic-secret",
                "sender": "sender@example.invalid",
                "recipients": ["to@example.invalid"],
            }
        )
    )
    return path


def test_lightweight_api_delivery_deduplicates_across_restarts(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(safe_event()))
    calls = []

    def api(req, timeout):
        calls.append(req)
        assert timeout == 15
        assert req.full_url == "https://api.resend.com/emails"
        return io.BytesIO(b'{"id":"synthetic-mail-id"}')

    monkeypatch.setattr(mail.request, "urlopen", api)
    secret = private_mail(tmp_path)
    state = tmp_path / "sent"
    assert mail.deliver(event, state, secret) == "sent"
    assert mail.deliver(event, state, secret) == "already_sent"
    assert len(calls) == 1
    assert "synthetic-secret" not in "".join(p.read_text() for p in state.glob("*.json"))


def test_mail_failure_retains_event_and_has_bounded_retries(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(safe_event()))
    before = event.read_bytes()
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("synthetic-secret-should-not-be-in-receipts")

    monkeypatch.setattr(mail.request, "urlopen", api)
    monkeypatch.setattr(mail.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="EVENT_RETAINED"):
        mail.deliver(event, tmp_path / "sent", private_mail(tmp_path))
    assert len(calls) == 2
    assert event.read_bytes() == before
    receipts = list((tmp_path / "sent").glob("*.json"))
    assert json.loads(receipts[0].read_bytes())["status"] == "failed"
    assert "synthetic-secret" not in receipts[0].read_text()


def test_mail_rejects_payload_fields_before_sending(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({**safe_event(), "price": "not-an-allowed-field"}))
    monkeypatch.setattr(mail.request, "urlopen", lambda *a, **k: pytest.fail("network attempted"))
    with pytest.raises(ValueError, match="EVENT_INVALID"):
        mail.deliver(event, tmp_path / "sent", tmp_path / "no-secret-needed")


def deployment(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    env = {}
    for key in (
        "BASE_ROOT",
        "HISTORY_ROOT",
        "WEEKLY_INPUT_ROOT",
        "RUNTIME_ROOT",
        "WEEKLY_JOB_ROOT",
        "TMP_ROOT",
    ):
        root = tmp_path / key
        root.mkdir()
        env["QUANTHUS_" + key] = str(root)
    for label in ("daily", "weekly"):
        (tmp_path / (label + ".json")).write_text("{}")
    env.update(
        QUANTHUS_DATA_UID="998",
        QUANTHUS_DATA_GID="998",
        QUANTHUS_DAILY_CONFIG=str(tmp_path / "daily.json"),
        QUANTHUS_WEEKLY_CONFIG=str(tmp_path / "weekly.json"),
    )
    compose = Path(__file__).parents[2] / "ops/data/compose.yaml"
    return dict(
        kind="p10-container-deployment-v1",
        revision=SHA,
        image="sha256:" + "b" * 64,
        compose=str(compose),
        compose_sha256=code.digest(compose),
        state=str(state),
        timeout_seconds=60,
        compose_env=env,
    )


def test_daemon_start_failure_still_notifies_on_host(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    config["mail_secret"] = "/not-mounted-in-container"

    def denied(*args, **kwargs):
        raise PermissionError("synthetic daemon denial")

    notices = []
    monkeypatch.setattr(host.subprocess, "Popen", denied)
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(host, "deliver", lambda *a, **k: notices.append(a) or "sent")
    with pytest.raises(RuntimeError, match="JOB_FAILED"):
        host.run(config, "daily", DAY)
    assert len(notices) == 1
    value = json.loads(notices[0][0].read_bytes())
    assert value["status"] == "failed"
    assert value["manifests"] == []
    scratch = list(Path(config["compose_env"]["QUANTHUS_TMP_ROOT"]).iterdir())
    assert len(scratch) == 1
    assert json.loads((scratch[0] / ".p10-tmp.json").read_bytes())["status"] == "failed"


def test_successful_dwd_is_not_rebuilt_on_email_failure(tmp_path, monkeypatch):
    config = deployment(tmp_path)
    config["mail_secret"] = "/host-only"
    calls = []

    def start(command, **kwargs):
        calls.append(command)
        assert kwargs["env"].get("QUANTHUS_RESEND_API_KEY") is None
        run_id = command[command.index("--name") + 1]
        scratch = Path(kwargs["env"]["QUANTHUS_JOB_TMP"])
        assert scratch.name == run_id and scratch.is_dir()
        target = Path(config["state"]) / "events" / (run_id + ".json")
        value = safe_event()
        value["run_id"] = run_id
        target.write_text(json.dumps(value))
        return SimpleNamespace(wait=lambda **k: 0, poll=lambda: 0)

    monkeypatch.setattr(host.subprocess, "Popen", start)

    def fail(*args, **kwargs):
        raise RuntimeError("email unavailable")

    monkeypatch.setattr(host, "deliver", fail)
    result = host.run(config, "daily", DAY)
    assert result["status"] == "complete"
    assert result["mail"] == "failed_event_retained"
    assert len(calls) == 1
    scratch = next(Path(config["compose_env"]["QUANTHUS_TMP_ROOT"]).iterdir())
    assert json.loads((scratch / ".p10-tmp.json").read_bytes())["status"] == "succeeded"


@pytest.mark.parametrize("mutation", ["image", "compose", "secret", "overlap"])
def test_deployment_checks_before_creating_any_state_outputs(tmp_path, mutation):
    value = deployment(tmp_path)
    if mutation == "image":
        value["image"] = "pipeline:latest"
    elif mutation == "compose":
        value["compose_sha256"] = "0" * 64
    elif mutation == "secret":
        value["compose_env"]["API_KEY"] = "must-not-pass-to-container"
    else:
        value["compose_env"]["QUANTHUS_BASE_ROOT"] = value["state"]
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        host.settings(config)
    assert not list(Path(value["state"]).iterdir())


def test_source_symlink_in_outbox_is_rejected_before_writes(tmp_path):
    config = deployment(tmp_path)
    source = Path(config["compose_env"]["QUANTHUS_BASE_ROOT"])
    (Path(config["state"]) / "events").symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="STATE_LINK"):
        host.run(config, "daily", DAY)
    assert not list(source.iterdir())


def test_weekly_runs_real_bounded_financial_build_and_verifier(tmp_path):
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    original, _, limits, sources = product(fixture_root)
    before = {str(p): code.digest(p) for p in sources if p.is_file()}
    plan = {
        "kind": "p10-container-weekly-financial-v1",
        "domains": ["financial_statements"],
        "execution_authorized": False,
        "code_revision": SHA,
        "through": "2026-10-17",
        "config": str(original / "weekly-config.json"),
        "config_sha256": "sha256:" + code.digest(original / "weekly-config.json"),
        "base": str(sources[0]),
        "source": str(original / "sources/financial_statements.parquet"),
        "receipts": str(original / "sources/financial_statements.parquet.receipts.parquet"),
        "source_control": str(original / "sources/datasets.json"),
        "job": str(tmp_path / "bounded-weekly"),
        "limits": asdict(limits),
        "expected_envelopes": 3,
    }
    for key, label in [
        ("source", "source_sha256"),
        ("receipts", "receipt_sha256"),
        ("source_control", "source_control_sha256"),
    ]:
        plan[label] = "sha256:" + code.digest(Path(plan[key]))
    path = tmp_path / "weekly-plan.json"
    path.write_text(json.dumps(plan))
    result = jobs.weekly(path, DAY, SHA)
    assert result["status"] == "WEEKLY_FINANCIAL_VERIFIED_OTHER_DOMAINS_HELD"
    manifest = json.loads((tmp_path / "bounded-weekly/product/manifest.json").read_bytes())
    assert manifest["complete_weekly_admission"] is False
    assert before == {str(p): code.digest(p) for p in sources if p.is_file()}
    plan["domains"].append("unknown-nonempty-source")
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="WEEKLY_SCOPE"):
        jobs.weekly(path, DAY, SHA)
