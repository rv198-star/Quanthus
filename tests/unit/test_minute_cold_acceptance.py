"""Cold acceptance is an isolated test, not a production deployment path."""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/bin/run-minute-cold-acceptance"
loader = importlib.machinery.SourceFileLoader("minute_cold_acceptance", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
cold = importlib.util.module_from_spec(spec)
loader.exec_module(cold)


@pytest.fixture
def metadata(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in cold.INPUTS:
        (source / name).write_bytes(name.encode())
    monkeypatch.setattr(cold.shutil, "disk_usage", lambda _: SimpleNamespace(free=16 * 1024**3))
    return source


def test_new_job_only_copies_named_metadata_not_minutes_or_ready(tmp_path, metadata):
    (metadata / "000001_1m.parquet").write_bytes(b"old minute")
    (metadata / "daily_ohlcv_none.parquet").write_bytes(b"old daily")
    allowed = tmp_path / "quarantine"
    job = allowed / "cold"
    inputs = cold.prepare_job(job, metadata, allowed_root=allowed)
    assert set(inputs) == set(cold.INPUTS)
    assert set(p.name for p in (job / "metadata").iterdir()) == set(cold.INPUTS)
    assert not (job / "shadow").exists()
    assert all(not p.stat().st_mode & 0o222 for p in (job / "metadata").iterdir())
    with pytest.raises(ValueError, match="already exists"):
        cold.prepare_job(job, metadata, allowed_root=allowed)


def test_escape_and_missing_metadata_fail_closed(tmp_path, metadata):
    with pytest.raises(ValueError, match="direct child"):
        cold.prepare_job(tmp_path / "production", metadata, allowed_root=tmp_path / "quarantine")
    (metadata / "stock_list.parquet").unlink()
    with pytest.raises(ValueError, match="metadata/snapshot missing"):
        cold.prepare_job(
            tmp_path / "quarantine/cold", metadata, allowed_root=tmp_path / "quarantine"
        )


def test_low_disk_preserves_old_evidence(tmp_path, metadata, monkeypatch):
    monkeypatch.setattr(cold.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="8 GiB"):
        cold.prepare_job(
            tmp_path / "quarantine/cold", metadata, allowed_root=tmp_path / "quarantine"
        )
    assert not (tmp_path / "quarantine/cold").exists()
    assert (metadata / "stock_list.parquet").is_file()


def test_inherited_production_options_cannot_restore_old_seed_or_output(tmp_path, monkeypatch):
    for key in (
        "HERMES_ODS_MINUTE_SEED_HOME",
        "HERMES_ODS_MINUTE_COLLECTOR",
        "HERMES_ODS_MINUTE_PAID_AUDIT_DIR",
        "HERMES_ODS_SYMBOL_LIMIT",
        "HERMES_ODS_MINUTE_1M_RECOVERY_POLICY",
        "QUANTHUS_DWD_DAILY_ROOT",
    ):
        monkeypatch.setenv(key, "/production/old")
    env = cold.isolated_environment(tmp_path / "cold", "/isolated/python")
    assert not any(
        key in env
        for key in (
            "HERMES_ODS_MINUTE_SEED_HOME",
            "HERMES_ODS_MINUTE_COLLECTOR",
            "HERMES_ODS_MINUTE_PAID_AUDIT_DIR",
            "HERMES_ODS_SYMBOL_LIMIT",
            "HERMES_ODS_MINUTE_1M_RECOVERY_POLICY",
            "QUANTHUS_DWD_DAILY_ROOT",
        )
    )
    assert env["ASTOCK_HOME"] == str(tmp_path / "cold/metadata")
    assert env["HERMES_ODS_ACQUIRE_MODE"] == "provider"
    assert env["ASTOCK_MINUTE_PYTHON"] == "/isolated/python"


@pytest.mark.skipif(os.getuid() != 0, reason="root-only credential role")
def test_credential_descriptor_rejects_world_readable_and_symlink(tmp_path):
    path = tmp_path / "credential"
    path.write_bytes(b"synthetic-test-value")
    path.chmod(0o600)
    with cold.protected_key(path) as handle:
        assert handle.fileno() >= 0
    path.chmod(0o644)
    with pytest.raises(ValueError, match="root-only"):
        cold.protected_key(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        cold.protected_key(link)


def test_harness_has_no_production_service_or_notification_calls():
    text = SCRIPT.read_text()
    assert "systemctl" not in text
    assert '--daily-root", str(job / "dwd")' in text
    assert "--resume" not in text
    assert "--force-preflight" not in text
    assert '--symbol"' not in text
