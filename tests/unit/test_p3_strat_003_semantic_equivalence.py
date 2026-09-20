from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.expert_evaluation.serialization import ExpertEvaluationError
from quanthus.expert_strategy import p3_strat_003_semantic_equivalence as probe
from quanthus.platform import event_worker

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p3-strat-003/semantic-equivalence-probe.v1.json"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _package(
    root: Path,
    *,
    market_date: date,
    suffix: str,
    consumer_suffix: str = "same",
    audit_suffix: str = "audit",
    prior: tuple[date, str, str] | None = None,
) -> tuple[Path, str]:
    package_id = f"dwd-daily-{market_date.strftime('%Y%m%d')}-aaaaaaaaaaaa-{suffix}"
    package = root / "packages" / market_date.isoformat() / package_id
    package.mkdir(parents=True)
    row_counts: dict[str, int] = {}
    files: list[dict[str, object]] = []
    for index, table in enumerate(probe._CONSUMER_TABLES, start=1):
        path = (
            package
            / "payload"
            / "fixture"
            / table
            / f"market_date={market_date.isoformat()}"
            / "part-00000.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{table}:{consumer_suffix}".encode())
        row_counts[table] = index
        files.append(
            {
                "path": path.relative_to(package).as_posix(),
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
    audit = package / "audit.json"
    audit.write_text(audit_suffix + "\n", encoding="ascii")
    files.append(
        {
            "path": "audit.json",
            "sha256": _sha(audit),
            "size_bytes": audit.stat().st_size,
        }
    )
    files.sort(key=lambda item: str(item["path"]))
    content_digest = "sha256:" + hashlib.sha256(_canonical(files)).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_domain_increment",
        "package_id": package_id,
        "market_date": market_date.isoformat(),
        "row_counts": row_counts,
        "files": files,
        "content_digest": content_digest,
        "selected_releases": {
            "calendar_daily_events": {
                "release_id": "hermes-calendar-daily-events-fixture",
                "manifest_sha256": "sha256:" + "1" * 64,
            },
            "intraday_1m": {
                "release_id": "hermes-intraday-archive-1m-fixture",
                "manifest_sha256": "sha256:" + "2" * 64,
            },
            "intraday_5m": {
                "release_id": "hermes-intraday-archive-5m-fixture",
                "manifest_sha256": "sha256:" + "3" * 64,
            },
            "market_daily_core": {
                "release_id": "hermes-market-daily-core-fixture",
                "manifest_sha256": "sha256:" + "4" * 64,
            },
        },
        "prior": None,
    }
    if prior is not None:
        manifest["prior"] = {
            "market_date": prior[0].isoformat(),
            "package_id": prior[1],
            "manifest_sha256": prior[2],
        }
    manifest_path = package / "manifest.json"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_sha = _sha(manifest_path)
    (package / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return package, manifest_sha


def test_probe_config_is_frozen_and_result_blind() -> None:
    config = probe._load_config(CONFIG)

    assert config.boundary.market_date == date(2026, 8, 27)
    assert tuple(item.sentinel_id for item in config.sentinels) == (
        "pre_builder_change_overlap",
        "post_builder_change_overlap",
    )
    assert config.raw["runtime_implementation"] == {
        "daily_domain": {
            "path": "src/quanthus/pipelines/securities_warehouse/daily_domain.py",
            "file_sha256": _sha(
                ROOT / "src/quanthus/pipelines/securities_warehouse/daily_domain.py"
            ),
        },
        "daily_ods_catalog": {
            "path": "src/quanthus/pipelines/securities_warehouse/daily_ods_catalog.py",
            "file_sha256": _sha(
                ROOT / "src/quanthus/pipelines/securities_warehouse/daily_ods_catalog.py"
            ),
        },
    }
    boundary = config.raw["missing_boundary"]
    assert boundary["source_identity_rule"] == "selected_cn01_release_self_verified"
    assert boundary["cross_source_physical_sha256_required"] is False
    assert boundary["required_ods_artifact"] == {
        "lane": "calendar_daily_events",
        "name": "financial_disclosures_delta",
        "row_count": 0,
    }
    assert config.raw["boundaries"] == {
        "strategy_or_economic_results": False,
        "target_outcome_labels": False,
        "candidate_scoring": False,
        "mutate_daily_current": False,
        "mutate_p6": False,
        "job_local_output_only": True,
    }


def test_consumer_identity_ignores_audit_only_manifest_drift(tmp_path: Path) -> None:
    left, left_manifest = _package(
        tmp_path / "left",
        market_date=date(2026, 8, 26),
        suffix="f8f8330",
        audit_suffix="historical-audit",
    )
    right, right_manifest = _package(
        tmp_path / "right",
        market_date=date(2026, 8, 26),
        suffix="abcdef0",
        audit_suffix="new-audit-with-ods-only-metadata",
    )

    left_digest, left_payload = probe._consumer_payload_identity(left)
    right_digest, right_payload = probe._consumer_payload_identity(right)

    assert left_manifest != right_manifest
    assert left_digest == right_digest
    assert left_payload == right_payload


def test_consumer_identity_detects_real_payload_difference(tmp_path: Path) -> None:
    left, _ = _package(
        tmp_path / "left",
        market_date=date(2026, 8, 26),
        suffix="f8f8330",
        consumer_suffix="left",
    )
    right, _ = _package(
        tmp_path / "right",
        market_date=date(2026, 8, 26),
        suffix="abcdef0",
        consumer_suffix="right",
    )

    left_digest, _ = probe._consumer_payload_identity(left)
    right_digest, _ = probe._consumer_payload_identity(right)

    assert left_digest != right_digest


def test_sentinel_selection_uses_latest_two_unique_verified_packages(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    day24, sha24 = _package(
        daily,
        market_date=date(2026, 8, 24),
        suffix=f"{24:07x}"[-7:],
    )
    day25, sha25 = _package(
        daily,
        market_date=date(2026, 8, 25),
        suffix=f"{25:07x}"[-7:],
        prior=(date(2026, 8, 24), day24.name, sha24),
    )
    _package(
        daily,
        market_date=date(2026, 8, 26),
        suffix=f"{26:07x}"[-7:],
        prior=(date(2026, 8, 25), day25.name, sha25),
    )
    spec = probe._SentinelSpec(
        sentinel_id="pre",
        start=date(2026, 8, 1),
        end=date(2026, 8, 26),
        required_package_count=2,
    )

    anchor, target = probe._select_sentinel_pair(daily, spec)

    assert anchor.market_date == date(2026, 8, 25)
    assert target.market_date == date(2026, 8, 26)


def test_sentinel_selection_fails_closed_when_original_overlap_is_insufficient(
    tmp_path: Path,
) -> None:
    daily = tmp_path / "daily"
    _package(
        daily,
        market_date=date(2026, 8, 26),
        suffix="f8f8330",
    )
    spec = probe._SentinelSpec(
        sentinel_id="pre",
        start=date(2026, 8, 1),
        end=date(2026, 8, 26),
        required_package_count=2,
    )

    with pytest.raises(ExpertEvaluationError, match="sentinel_missing"):
        probe._select_sentinel_pair(daily, spec)


def test_missing_boundary_binding_is_read_from_real_child_manifest(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    missing_id = "dwd-daily-20260827-aaaaaaaaaaaa-f8f8330"
    missing_sha = "sha256:" + "9" * 64
    _package(
        daily,
        market_date=date(2026, 8, 28),
        suffix="abcdef0",
        prior=(date(2026, 8, 27), missing_id, missing_sha),
    )

    assert probe._missing_child_binding(daily, date(2026, 8, 27)) == (
        missing_id,
        missing_sha,
    )


def test_event_worker_admits_probe_as_input_only_structure_job() -> None:
    run_config = "config/research/p3-strat-003/semantic-equivalence-probe.v1.json"
    input_ref = {
        "code_revision": "a" * 40,
        "run_config": run_config,
    }

    assert event_worker._structure_input(input_ref) == input_ref
    assert event_worker._structure_request_digest(run_config).startswith("sha256:")
