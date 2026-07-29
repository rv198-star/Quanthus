from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import quanthus.pipelines.ods_handoff as handoff

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "config" / "pipelines" / "ods-handoff-sources.v1.json"


def test_repository_sources_prioritize_cn01_then_hermes() -> None:
    config = handoff.load_ods_source_config(SOURCES)

    assert [(source.source_id, source.priority) for source in config.sources] == [
        ("cn01", 0),
        ("hermes", 1),
    ]
    assert config.sources[0].selection == "latest_ready_attempt"
    assert config.sources[0].observed_transfer_max_duration_seconds == 3.649
    assert config.sources[0].transfer_sla_seconds == 8
    assert config.sources[1].selection == "current_or_flat"
    assert config.sources[1].transfer_sla_seconds is None


def test_same_poll_prefers_cn01_and_records_an_immutable_attempt(
    monkeypatch, tmp_path: Path
) -> None:
    config = handoff.load_ods_source_config(SOURCES)
    seen: list[str] = []

    monkeypatch.setattr(
        handoff,
        "resolve_ods_source",
        lambda source: SimpleNamespace(source=source),
    )

    def probe(source, *, schedule: str, market_date: str):
        seen.append(source.source.source_id)
        return "attempt", f"{source.source.source_id}-20260727T000000Z"

    monkeypatch.setattr(handoff, "probe_ods_source", probe)
    selection_path = tmp_path / "selection.json"

    result = handoff.wait_for_ods_ready(
        source_config=config,
        schedule="daily",
        market_date="2026-07-27",
        selection_path=selection_path,
        timeout_seconds=1,
        poll_seconds=1,
    )

    assert result.source_id == "cn01"
    assert result.attempt_id == "cn01-20260727T000000Z"
    assert seen == ["cn01"]
    payload = selection_path.read_text(encoding="utf-8")
    assert '"source_id": "cn01"' in payload


def test_invalid_cn01_probe_does_not_block_a_ready_hermes_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    config = handoff.load_ods_source_config(SOURCES)
    monkeypatch.setattr(
        handoff,
        "resolve_ods_source",
        lambda source: SimpleNamespace(source=source),
    )

    def probe(source, *, schedule: str, market_date: str):
        if source.source.source_id == "cn01":
            raise handoff.OdsHandoffError("bad current pointer")
        return "attempt", "hermes-20260727T000000Z"

    monkeypatch.setattr(handoff, "probe_ods_source", probe)

    result = handoff.wait_for_ods_ready(
        source_config=config,
        schedule="daily",
        market_date="2026-07-27",
        selection_path=tmp_path / "selection.json",
        timeout_seconds=1,
        poll_seconds=1,
    )

    assert result.source_id == "hermes"
    assert result.attempt_id == "hermes-20260727T000000Z"


def test_daily_manifest_requires_market_calendar_artifact(tmp_path: Path) -> None:
    manifest = tmp_path / "release.json"
    payload = {
        "schema_version": 1,
        "schedule": "daily",
        "market_date": "2026-07-29",
        "status": "ready",
        "artifacts": [
            {
                "name": "announcements_delta",
                "relative_path": "artifacts/announcements_delta.parquet",
            }
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert not handoff._is_market_daily_manifest(
        manifest, market_date="2026-07-29"
    )

    payload["artifacts"].append(
        {
            "name": "trading_calendar",
            "relative_path": "artifacts/ref_trading_calendar.parquet",
        }
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert handoff._is_market_daily_manifest(manifest, market_date="2026-07-29")


def test_latest_daily_attempt_skips_newer_calendar_events_release(monkeypatch) -> None:
    source = SimpleNamespace()
    monkeypatch.setattr(
        handoff,
        "_attempt_directories",
        lambda *_args, **_kwargs: ["calendar-events", "market-daily"],
    )
    seen: list[str] = []

    def ready(_source, *, schedule: str, market_date: str, attempt_id: str) -> bool:
        seen.append(attempt_id)
        return attempt_id == "market-daily"

    monkeypatch.setattr(handoff, "_probe_ready_attempt", ready)

    assert handoff._probe_latest_ready_attempt(
        source, schedule="daily", market_date="2026-07-29"
    ) == "market-daily"
    assert seen == ["calendar-events", "market-daily"]


def test_current_calendar_events_attempt_falls_back_to_market_attempt(monkeypatch) -> None:
    configured = SimpleNamespace(selection="current_or_flat")
    source = SimpleNamespace(source=configured)
    monkeypatch.setattr(handoff, "_probe_current", lambda *_args, **_kwargs: "events")
    monkeypatch.setattr(
        handoff,
        "_probe_ready_attempt",
        lambda *_args, attempt_id, **_kwargs: attempt_id != "events",
    )
    monkeypatch.setattr(
        handoff,
        "_probe_latest_ready_attempt",
        lambda *_args, **_kwargs: "market",
    )

    assert handoff.probe_ods_source(
        source, schedule="daily", market_date="2026-07-29"
    ) == ("attempt", "market")
