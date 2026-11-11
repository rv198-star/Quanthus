"""P10 OCI mirror email gate reads independently from authorized CN01 jail."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.pipelines import p10_deadline_cycle as cycle

TARGET = date(2026, 10, 8)
DIGEST = "sha256:" + "d" * 64


def _fake_authority(destination: Path, *, day: date = TARGET, sha: str = DIGEST) -> None:
    destination.write_text(json.dumps({
        "kind": "cn01_daily_core_dwd_current",
        "schema_version": 2,
        "product_scope": "daily-core-v1",
        "market_date": day.isoformat(),
        "manifest_path": f"packages/{day}/fixed-validated-id/manifest.json",
        "manifest_sha256": sha,
    }))


def test_oci_mail_uses_real_readonly_cn01_date_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def remote_fetch(*, source: str, destination: Path, ssh: str) -> None:
        assert source.endswith("/2026-10-08.json")
        assert ssh == "ssh-readonly-known-hosts"
        calls.append(source)
        _fake_authority(destination)

    monkeypatch.setattr(
        cycle, "_ssh_command",
        lambda: ("cn01.example", "22", "quanthus-dwd-ro", "/tmp/key",
                 "/tmp/known_hosts", "ssh-readonly-known-hosts"),
    )
    monkeypatch.setattr(
        cycle, "_remote_path", lambda relative: f"products/daily-core/{relative}"
    )
    monkeypatch.setattr(cycle, "_run_rsync", remote_fetch)
    assert cycle._source_cn01_daily_digest(TARGET) == DIGEST
    assert len(calls) == 1


def test_oci_rejects_wrong_date_source_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cycle, "_ssh_command",
        lambda: ("cn01.example", "22", "quanthus-dwd-ro", "/tmp/key",
                 "/tmp/known_hosts", "ssh-readonly-known-hosts"),
    )
    monkeypatch.setattr(
        cycle, "_remote_path", lambda relative: f"products/daily-core/{relative}"
    )
    monkeypatch.setattr(
        cycle, "_run_rsync",
        lambda *, source, destination, ssh: _fake_authority(
            destination, day=date(2026, 10, 7)
        ),
    )
    with pytest.raises(Exception, match="date"):
        cycle._source_cn01_daily_digest(TARGET)
