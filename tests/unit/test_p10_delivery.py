"""Event-driven delivery does not recollect, reopen permissions or alter data."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus import p10_delivery as delivery
from quanthus import p10_gdrive_backup as drive
from quanthus.pipelines.securities_warehouse import container_jobs
from tests.unit.test_p10_container_delivery import DAY, SHA


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True))


@pytest.fixture
def ready(tmp_path):
    runtime, events, state, mail = [tmp_path / n for n in ("runtime", "events", "state", "mail")]
    for p in (runtime, events, state, mail):
        p.mkdir()
    run = runtime / "run"
    run.mkdir()
    roots = {n: run / n for n in ("product", "core", "market", "events", "native")}
    for n, p in roots.items():
        p.mkdir()
        save(p / ("release.json" if n in {"market", "events"} else "manifest.json"), {"scope": n})
        (p / "data").write_bytes(b"synthetic " + n.encode())
        (p / "READY").write_text("synthetic ready")

    def h(n):
        return "sha256:" + drive.digest(
            roots[n] / ("release.json" if n in {"market", "events"} else "manifest.json")
        )

    base = json.loads(drive.POLICY.read_bytes())["base_manifest_sha256"]
    entry = {
        "execution_authorized": False,
        "base_manifest_sha256": base,
        "product": str(roots["product"]),
        "core": str(roots["core"]),
        "native_source": str(roots["native"]),
        "native_manifest_sha256": h("native"),
        "core_manifest_sha256": h("core"),
        "product_manifest_sha256": h("product"),
        "source_manifest_sha256": {
            "market_daily_core": h("market"),
            "calendar_daily_events": h("events"),
        },
    }
    for label in ("market", "events"):
        save(run / (label + "-source.json"), {"release_dir": str(roots[label])})
    save(runtime / "DATES.json", {"dates": {str(DAY): entry}})
    event = container_jobs.event(
        "daily", DAY, "daily-synthetic", SHA, {**entry, "status": "QUALIFIED_1D_NATIVE5M"}
    )
    event_path = events / "daily.json"
    save(event_path, event)
    route = {
        "kind": "p10-base24-delivery-routing-v1",
        "base_manifest_sha256": base,
        "code_revision": SHA,
        "notify_statuses": ["complete", "partial", "closed", "busy", "failed"],
        "folder_ids": {"ODS": "exact-ods-folder", "DWD": "exact-dwd-folder"},
        "remote_name": "gdrive",
        "limits": {
            "max_source_bytes": 1024**2,
            "max_archive_bytes": 1024**2,
            "free_floor_bytes": 1024,
            "timeout_seconds": 60,
        },
    }
    path = tmp_path / "routing.json"
    save(path, route)
    return route, path, runtime, events, state, mail, event, roots


def test_selection_separates_layers_matches_manifest_and_keeps_bytes(ready):
    route, _, runtime, _, state, _, event, roots = ready
    before = {str(p): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    result = delivery.freeze_daily(event, route, runtime, SHA)
    assert [(p["id"], p["layer"]) for p in result["packages"]] == [
        ("dwd-product", "DWD"),
        ("ods-market", "ODS"),
        ("ods-events", "ODS"),
        ("ods-native5m-acquisition", "ODS"),
    ]
    selected = state.parent / "selection.json"
    save(selected, result)
    assert drive.selection(selected, state)[0] == result
    assert before == {str(p): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}


@pytest.mark.parametrize("change", ["manifest", "outside", "entry", "gate"])
def test_stale_event_unknown_path_or_changed_source_is_rejected(ready, tmp_path, change):
    route, _, runtime, _, _, _, event, roots = ready
    if change == "manifest":
        (roots["product"] / "manifest.json").write_text("{}")
    else:
        f = runtime / "DATES.json"
        d = json.loads(f.read_bytes())
        e = d["dates"][str(DAY)]
        if change == "outside":
            e["product"] = str(tmp_path)
        elif change == "gate":
            e["execution_authorized"] = True
        else:
            e["product_manifest_sha256"] = "sha256:" + "0" * 64
        save(f, d)
    with pytest.raises(ValueError):
        delivery.freeze_daily(event, route, runtime, SHA)


def test_failure_mail_and_backup_retry_are_independent_and_no_repacking_after_verified(
    ready, monkeypatch
):
    route, path, runtime, events, state, mail, event, _ = ready
    calls = []
    monkeypatch.setattr(delivery, "notify", lambda *a: "sent")

    def backup(*args):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("must not log provider-secret-value")
        return {"selection_sha256": "b" * 64}

    monkeypatch.setattr(drive, "backup", backup)
    args = (path, runtime, events, state, mail, Path("/unused"), Path("/unused"), SHA)
    first = delivery.drain(*args)
    assert first["events"][0]["mail"] == "sent"
    assert first["events"][0]["backup"] == "failed_retained_for_retry"
    frozen = next(state.glob("*.selection.json"))
    before = frozen.read_bytes()
    assert delivery.drain(*args)["events"][0]["backup"] == "cloud_bytes_verified"
    assert delivery.drain(*args)["events"][0]["backup"] == "already_verified"
    assert len(calls) == 2 and frozen.read_bytes() == before
    assert "provider-secret-value" not in (state / "DELIVERY.json").read_text()


def test_failure_event_sends_mail_without_data_selection(ready, monkeypatch):
    _, path, runtime, events, state, mail, _, _ = ready
    event = container_jobs.event(
        "daily", DAY, "daily-failed", SHA, {"status": "CONTAINER_RUN_FAILED"}
    )
    save(events / "daily.json", event)
    seen = []
    monkeypatch.setattr(delivery, "notify", lambda *a: seen.append(a[0]) or "sent")
    monkeypatch.setattr(delivery, "freeze_daily", lambda *a: pytest.fail("no data on failure"))
    result = delivery.drain(path, runtime, events, state, mail, Path("unused"), Path("unused"), SHA)
    assert seen and result["events"][0]["backup"] == "no_qualified_data_scope"


def test_existing_broker_config_token_not_in_argv_or_public_output(tmp_path, monkeypatch):
    path = tmp_path / "oauth-bridge.json"
    save(
        path,
        {
            "authid": "existing-synthetic-auth",
            "refresh_secret": "existing-secret",
            "oauth_url": "https://duplicati-oauth-handler.appspot.com/refresh",
        },
    )

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    seen = []

    def opened(req, **kwargs):
        assert req.full_url == "https://duplicati-oauth-handler.appspot.com/refresh"
        assert kwargs["timeout"] <= 60
        return Response(b'{"access_token":"synthetic-access-token"}')

    monkeypatch.setattr(drive.request, "build_opener", lambda *a: SimpleNamespace(open=opened))

    def run(command, **kwargs):
        seen.append(command)
        assert "synthetic-access-token" not in str(command)
        assert kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:10800"
        assert "--no-check-certificate" not in command
        kwargs["stdout"].write(b"[]")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(drive.subprocess, "run", run)
    assert (
        drive.cloud(
            ["lsjson", "gdrive:"], {"remote_name": "gdrive"}, path, drive.time.monotonic() + 60
        )
        == "[]"
    )
    assert seen and (tmp_path / "rclone.conf").stat().st_mode & 0o777 == 0o600
    assert "existing-secret" not in (tmp_path / "rclone.conf").read_text()


def test_broker_does_not_contact_unknown_refresh_host(tmp_path, monkeypatch):
    path = tmp_path / "oauth-bridge.json"
    save(path, {"authid": "secret", "refresh_secret": "secret", "oauth_url": "http://evil.invalid"})
    monkeypatch.setattr(drive.request, "build_opener", lambda *a: pytest.fail("no network"))
    with pytest.raises(ValueError):
        drive.bridge_config(path, "gdrive", drive.time.monotonic() + 60)
