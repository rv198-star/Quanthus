from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.source import (
    EventScanSourceError,
    load_event_scan_source_policy,
    verify_event_scan_base,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "config" / "p4-event-scan-source.research.v1.json"
RELEASE_PATH = ROOT / "config" / "releases" / "v2-build202607.json"
PROVIDER_PATH = ROOT / "config" / "replay-data-provider.development.v2.json"


def _file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_digest(raw: dict[str, object]) -> str:
    payload = {
        key: value
        for key, value in raw.items()
        if key not in {"decision_reference", "semantic_digest"}
    }
    return sha256_digest(payload)


def _build_base(root: Path) -> tuple[Path, str, str]:
    package = root / "dwd-base-202607-v2.1"
    payload = package / "payload" / "security_market_observation" / "part-00000.parquet"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"synthetic-dwd-base-v2.1")
    required = [
        "dwd_security",
        "dwd_listing",
        "dwd_trading_calendar",
        "dwd_security_bar",
        "dwd_tradability_state",
        "dwd_price_limit_regime",
        "dwd_adjustment_factor",
        "dwd_company_industry_membership",
        "dwd_industry_node",
    ]
    rows = {name: 1 for name in required}
    for index in range(33 - len(rows)):
        rows[f"dwd_test_filler_{index:02d}"] = index + 1
    content_digest = "sha256:" + "a" * 64
    manifest = {
        "schema_version": 1,
        "kind": "dwd_domain_base_package",
        "package_id": package.name,
        "row_counts": rows,
        "files": [
            {
                "path": payload.relative_to(package).as_posix(),
                "sha256": _file_sha(payload),
                "size_bytes": payload.stat().st_size,
            }
        ],
        "payload_bytes": payload.stat().st_size,
        "payload_file_count": 1,
        "content_digest": content_digest,
    }
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    manifest_sha = _file_sha(package / "manifest.json")
    (package / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return package, manifest_sha, content_digest


def _write_policy(
    root: Path,
    *,
    manifest_sha: str,
    content_digest: str,
    mutate: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["base_release"]["manifest_sha256"] = manifest_sha
    raw["base_release"]["content_digest"] = content_digest
    if mutate is not None:
        mutate(raw)
    raw["semantic_digest"] = _semantic_digest(raw)
    path = root / "source-policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class EventScanSourceTests(unittest.TestCase):
    def test_repository_policy_matches_v21_correction_and_direct_provider(self) -> None:
        policy = load_event_scan_source_policy(
            POLICY_PATH,
            release_path=RELEASE_PATH,
            provider_path=PROVIDER_PATH,
        )

        self.assertEqual(policy.authority, "direct_dwd_v2_1_plus_correction")
        self.assertEqual(policy.status, "approved_development_only")
        self.assertEqual(policy.base_release.package_id, "dwd-base-202607-v2.1")
        self.assertEqual(
            policy.correction_release.correction_id,
            "dwd-base-202607-v2.1-correction-20260813-001",
        )
        self.assertEqual(policy.daily_increment_mode, "disabled_for_initial_development_scan")
        self.assertEqual(
            policy.static_industry_reference.authority,
            "p4_adm_dwd_plus_checksum_bound_historical_industry",
        )
        self.assertEqual(
            policy.static_industry_reference.source_tables,
            ("dwd_company_industry_membership", "dwd_industry_node"),
        )
        self.assertEqual(
            policy.historical_visibility.status,
            "approved_development_retrospective",
        )
        self.assertFalse(policy.historical_visibility.per_decision_publication_proven)
        policy.historical_visibility.require_full_scan_authority()
        self.assertEqual(policy.semantic_digest, policy.computed_semantic_digest)
        self.assertIn("legacy_operational_ads", policy.forbidden_sources)
        self.assertIn("permanent_blind", policy.historical_visibility.forbidden_uses)
        with self.assertRaisesRegex(EventScanSourceError, "set QUANTHUS_REPLAY_PROVIDER_DWD_ROOT"):
            policy.resolve_runtime_root({})
        with self.assertRaisesRegex(
            EventScanSourceError,
            "set QUANTHUS_REPLAY_PROVIDER_DWD_CORRECTION_ROOT",
        ):
            policy.resolve_correction_root({})
        with self.assertRaisesRegex(EventScanSourceError, "outside the authorized"):
            policy.require_development_date(date(2022, 1, 1))

    def test_development_projection_is_table_specific_and_retains_pit_limit(self) -> None:
        policy = load_event_scan_source_policy(POLICY_PATH)
        visibility = policy.historical_visibility
        close = datetime(2018, 5, 8, 7, 0, tzinfo=timezone.utc)
        effective = datetime(2010, 1, 4, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            visibility.project_candidate_knowledge(
                table="dwd_security_bar",
                session_close=close,
            ),
            close,
        )
        self.assertEqual(
            visibility.project_candidate_knowledge(
                table="dwd_tradability_state",
                session_close=close,
            ),
            close,
        )
        self.assertEqual(
            visibility.project_candidate_knowledge(
                table="dwd_listing",
                session_close=close,
                effective_from=effective,
            ),
            effective,
        )
        self.assertEqual(
            visibility.project_candidate_knowledge(
                table="dwd_adjustment_factor",
                session_close=close,
                effective_from=close,
            ),
            close,
        )
        with self.assertRaisesRegex(EventScanSourceError, "after the candidate session close"):
            visibility.project_candidate_knowledge(
                table="dwd_security",
                session_close=close,
                effective_from=datetime(2018, 5, 9, 0, 0, tzinfo=timezone.utc),
            )
        with self.assertRaisesRegex(EventScanSourceError, "not a candidate T-close"):
            visibility.project_candidate_knowledge(
                table="dwd_price_limit_regime",
                session_close=close,
                effective_from=effective,
            )

    def test_exact_physical_v21_base_passes_deep_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, manifest_sha, content_digest = _build_base(root)
            policy = load_event_scan_source_policy(
                _write_policy(
                    root,
                    manifest_sha=manifest_sha,
                    content_digest=content_digest,
                )
            )

            result = verify_event_scan_base(
                policy,
                environment={"QUANTHUS_REPLAY_PROVIDER_DWD_ROOT": str(package)},
            )

            self.assertEqual(result.package_id, "dwd-base-202607-v2.1")
            self.assertEqual(result.table_count, 33)
            self.assertEqual(result.manifest_sha256, manifest_sha)

    def test_mutable_pointer_wrong_package_and_unknown_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, manifest_sha, content_digest = _build_base(root)
            policy_path = _write_policy(
                root,
                manifest_sha=manifest_sha,
                content_digest=content_digest,
            )
            policy = load_event_scan_source_policy(policy_path)
            pointer = root / "CURRENT.json"
            pointer.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(EventScanSourceError, "package id differs"):
                policy.resolve_runtime_root(
                    {"QUANTHUS_REPLAY_PROVIDER_DWD_ROOT": str(pointer)}
                )
            legacy = root / "legacy-operational-ads"
            legacy.mkdir()
            with self.assertRaisesRegex(EventScanSourceError, "package id differs"):
                policy.resolve_runtime_root(
                    {"QUANTHUS_REPLAY_PROVIDER_DWD_ROOT": str(legacy)}
                )

            raw = json.loads(policy_path.read_text(encoding="utf-8"))
            raw["legacy_fallback"] = str(package)
            invalid = root / "invalid.json"
            invalid.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(EventScanSourceError, "unexpected"):
                load_event_scan_source_policy(invalid)

    def test_provider_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = json.loads(PROVIDER_PATH.read_text(encoding="utf-8"))
            provider["execution_authorized"] = True
            provider_path = root / "provider.json"
            provider_path.write_text(json.dumps(provider), encoding="utf-8")

            with self.assertRaisesRegex(EventScanSourceError, "research-only direct DWD provider"):
                load_event_scan_source_policy(POLICY_PATH, provider_path=provider_path)


if __name__ == "__main__":
    unittest.main()
