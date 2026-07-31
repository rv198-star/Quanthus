from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

try:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover - exercised by the P1 dependency job
    duckdb = None
    pa = None
    pq = None

from quanthus.corporate_action_normalization import load_corporate_action_normalization_policy
from quanthus.operational_ads_base import (
    OperationalADSBaseError,
    _logical_schema,
    _package_content_identity,
    _parquet_content_digest,
    _validate_ledger_decision_calendar,
    _verify_dwd_dws_input_ledger,
    build_operational_ads_base,
    load_operational_ads_base_current,
    load_operational_ads_base_plan,
    load_operational_ads_base_policy,
    load_operational_ads_replay_reader,
    open_operational_ads_base,
    preflight_operational_ads_base_upstreams,
    promote_operational_ads_base,
    smoke_operational_ads_replay,
    verify_operational_ads_base,
)

SOURCE_CONTENT = f"sha256:{'1' * 64}"
SOURCE_LINEAGE = f"sha256:{'2' * 64}"
SOURCE_GENERATION = f"sha256:{'3' * 64}"
PIT_EVIDENCE = f"sha256:{'4' * 64}"
OUTCOME_CONTENT = f"sha256:{'5' * 64}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


@unittest.skipIf(
    duckdb is None or pa is None or pq is None,
    "P1 dependencies are not installed",
)
class OperationalADSBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy_path = self.root / "policy.json"
        self.source = self.root / "source-export"
        self.source.mkdir()
        self._write_policy()
        self.policy = load_operational_ads_base_policy(self.policy_path)
        self._write_source()
        self.plan_path = self.root / "plan.json"
        self._write_plan()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_policy(self) -> None:
        self._write_json(
            self.policy_path,
            {
                "schema_version": 1,
                "contract_id": "operational-ads-base-test-v1",
                "profile": "strategy_replay_test_v1",
                "authority": {
                    "holder": "oci",
                    "research_only": True,
                    "execution_authorized": False,
                },
                "packages": {
                    "provisional": {
                        "package_id": "ads-base-test-v1",
                        "through_date": "2024-01-03",
                    },
                    "final": {
                        "package_id": "ads-base-test-final-v1",
                        "through_date": "2026-07-31",
                    },
                },
                "required_upstream_roles": ["canonical_catalog", "dwd_base", "dws_base"],
                "generation_ledger_calendar_table": "trading_calendar",
                "tables": {
                    "benchmark_curve": {
                        "role": "outcome_only",
                        "format": "json",
                        "row_path": "observations",
                        "date_column": "date",
                        "business_key": ["date"],
                        "sort_keys": ["date"],
                        "allow_empty": False,
                        "require_per_decision_pit": False,
                    },
                    "corporate_action_exceptions": {
                        "role": "runner_only",
                        "format": "parquet",
                        "date_column": "blocked_from",
                        "business_key": ["exception_id"],
                        "sort_keys": ["blocked_from", "security_code", "exception_id"],
                        "allow_empty": True,
                        "require_per_decision_pit": False,
                    },
                    "corporate_actions_normalized": {
                        "role": "runner_only",
                        "format": "parquet",
                        "date_column": "effective_date",
                        "business_key": ["action_id"],
                        "sort_keys": ["effective_date", "security_code", "action_id"],
                        "allow_empty": False,
                        "require_per_decision_pit": False,
                    },
                    "daily_market_summary": {
                        "role": "decision_visible",
                        "format": "parquet",
                        "date_column": "market_date",
                        "business_key": ["market_date"],
                        "sort_keys": ["market_date"],
                        "allow_empty": False,
                        "require_per_decision_pit": True,
                    },
                    "daily_ohlcv_none": {
                        "role": "decision_visible",
                        "format": "parquet",
                        "date_column": "date",
                        "business_key": ["date", "security_id"],
                        "sort_keys": ["date", "security_id"],
                        "allow_empty": False,
                        "require_per_decision_pit": True,
                    },
                    "daily_universe": {
                        "role": "decision_visible",
                        "format": "parquet",
                        "date_column": "market_date",
                        "business_key": ["market_date", "security_id"],
                        "sort_keys": ["market_date", "security_id"],
                        "allow_empty": False,
                        "require_per_decision_pit": True,
                    },
                    "price_limit_execution_daily": {
                        "role": "runner_only",
                        "format": "parquet",
                        "date_column": "execution_date",
                        "business_key": ["execution_date", "security_id"],
                        "sort_keys": ["execution_date", "security_id"],
                        "allow_empty": False,
                        "require_per_decision_pit": False,
                    },
                    "security_code_validity": {
                        "role": "runner_only",
                        "format": "parquet",
                        "date_column": "valid_from",
                        "business_key": ["security_id", "stock_code", "valid_from"],
                        "sort_keys": ["valid_from", "security_id", "stock_code"],
                        "allow_empty": False,
                        "require_per_decision_pit": False,
                    },
                    "static_industry_mapping": {
                        "role": "runner_policy_reference",
                        "format": "json",
                        "row_path": "memberships",
                        "date_column": None,
                        "business_key": ["security_id"],
                        "sort_keys": ["security_id"],
                        "allow_empty": False,
                        "require_per_decision_pit": False,
                    },
                    "trading_calendar": {
                        "role": "runner_only",
                        "format": "parquet",
                        "date_column": "trade_date",
                        "business_key": ["trade_date", "exchange"],
                        "sort_keys": ["trade_date", "exchange"],
                        "allow_empty": False,
                        "require_per_decision_pit": False,
                    },
                },
                "consumer_roles": {
                    "replay_decision_reader": ["decision_visible"],
                    "replay_evaluator": ["outcome_only"],
                    "replay_runner": [
                        "decision_visible",
                        "runner_only",
                        "runner_policy_reference",
                    ],
                },
                "publication": {
                    "artifact_discovery": "manifest_tables_parts",
                    "mandatory_year_partitioning": False,
                    "ready_written_last": True,
                    "descriptor_compare_and_swap": True,
                    "migration_mode": "copy_only",
                    "delete_enabled": False,
                },
            },
        )

    def _parquet_identity(
        self, path: Path, sort_keys: tuple[str, ...]
    ) -> tuple[str, dict[str, object]]:
        assert duckdb is not None
        connection = duckdb.connect(":memory:")
        try:
            relation = f"read_parquet('{path}')"
            description = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
            columns = tuple(str(item[0]) for item in description)
            digest, fingerprint = _parquet_content_digest(
                connection,
                relation,
                columns=columns,
                schema=_logical_schema(description),
                sort_keys=sort_keys,
            )
            return digest, fingerprint
        finally:
            connection.close()

    def _write_source(self, *, duplicate_daily_key: bool = False) -> None:
        assert pa is not None
        assert pq is not None
        dates = (date(2024, 1, 2), date(2024, 1, 3))
        security_id = "SSE-600000"
        daily_rows = [
            {
                "date": market_date,
                "security_id": security_id,
                "open": Decimal("10.00"),
                "high": Decimal("10.60"),
                "low": Decimal("9.90"),
                "close": Decimal("10.50"),
                "volume": Decimal("100000"),
                "amount": Decimal("1000000"),
            }
            for market_date in dates
        ]
        if duplicate_daily_key:
            daily_rows.append(dict(daily_rows[-1]))
        action_policy = load_corporate_action_normalization_policy()
        corporate_lineage = {
            "normalization_policy_version": action_policy.policy_version,
            "normalization_report_digest": f"sha256:{'a' * 64}",
            "corporate_action_source_digest": f"sha256:{'b' * 64}",
            "authority_source_digest": f"sha256:{'c' * 64}",
            "exception_ledger_digest": f"sha256:{'d' * 64}",
            "exception_ledger_version": str(action_policy.exception_ledger_version),
        }
        parquet_rows = {
            "corporate_action_exceptions": [
                {
                    "exception_id": "exception-1",
                    "security_code": "600000",
                    "blocked_from": dates[-1],
                    "blocked_through": dates[-1],
                    "reason_code": "normalization_unresolved",
                    "replay_disposition": "security_fail_closed",
                    "execution_authorized": False,
                }
            ],
            "corporate_actions_normalized": [
                {
                    "action_id": "action-1",
                    "security_code": "600000",
                    "effective_date": dates[-1],
                    "known_at": datetime.fromisoformat("2024-01-02T10:00:00+08:00"),
                    "cash_dividend_per_share": Decimal("0.10"),
                    "bonus_share_ratio": Decimal("0.00"),
                    "source_revision": "action-source-v1",
                    "source_snapshot_id": "action-snapshot-v1",
                    "evidence_digest": f"sha256:{'e' * 64}",
                    "execution_authorized": False,
                }
            ],
            "daily_market_summary": [
                {"market_date": market_date, "security_count": 1} for market_date in dates
            ],
            "daily_ohlcv_none": daily_rows,
            "daily_universe": [
                {
                    "security_id": security_id,
                    "stock_code": "600000",
                    "exchange": "SSE",
                    "market_date": market_date,
                }
                for market_date in dates
            ],
            "price_limit_execution_daily": [
                {
                    "security_id": security_id,
                    "exchange": "SSE",
                    "execution_date": market_date,
                    "observed_tradestatus": "1",
                    "entry_policy_allows": True,
                    "is_executable": True,
                    "reason_code": "price_limit_resolved",
                    "upper_limit_pct": Decimal("10"),
                    "lower_limit_pct": Decimal("10"),
                    "upper_limit_price": Decimal("11.55"),
                    "lower_limit_price": Decimal("9.45"),
                }
                for market_date in dates
            ],
            "security_code_validity": [
                {
                    "security_id": security_id,
                    "stock_code": "600000",
                    "exchange": "SSE",
                    "valid_from": "1990-12-19",
                    "valid_to": "2024-12-31",
                }
            ],
            "trading_calendar": [
                {"trade_date": market_date, "exchange": exchange, "is_open": 1}
                for market_date in dates
                for exchange in ("SSE", "SZSE")
            ],
        }
        table_values = []
        for name, rows in sorted(parquet_rows.items()):
            contract = self.policy.tables[name]
            path = self.source / f"{name}.parquet"
            pq.write_table(pa.Table.from_pylist(rows), path)
            if name == "corporate_actions_normalized":
                revisions = corporate_lineage
            elif name == "corporate_action_exceptions":
                revisions = {**corporate_lineage, "replay_disposition": "security_fail_closed"}
            else:
                revisions = {"source": f"test-{name}-v1"}
            lineage = {
                "lineage_digest": SOURCE_LINEAGE,
                "manifest_id": "synthetic-export-v1-manifest",
                "knowledge_cutoff": "2024-01-03T18:00:00+08:00",
                "source_revisions": revisions,
            }
            if contract.require_per_decision_pit:
                lineage["pit_evidence_digest"] = PIT_EVIDENCE
            content_digest, fingerprint = self._parquet_identity(path, contract.sort_keys)
            schema_bytes = pq.ParquetFile(path).schema_arrow.serialize().to_pybytes()
            date_values = (
                [date.fromisoformat(str(row[contract.date_column])) for row in rows]
                if contract.date_column is not None
                else []
            )
            table_values.append(
                {
                    "name": name,
                    "role": contract.role,
                    "table_content_digest": content_digest,
                    "content_fingerprint": fingerprint,
                    "source_lineage": lineage,
                    "source_manifest": {
                        "manifest_id": "synthetic-export-v1-manifest",
                        "knowledge_cutoff": "2024-01-03T18:00:00+08:00",
                        "source_revisions": revisions,
                    },
                    "parts": [
                        {
                            "path": path.name,
                            "sha256": _sha256(path),
                            "size_bytes": path.stat().st_size,
                            "row_count": len(rows),
                            "schema_sha256": (
                                f"sha256:{hashlib.sha256(schema_bytes).hexdigest()}"
                            ),
                            "min_date": min(date_values).isoformat() if date_values else None,
                            "max_date": max(date_values).isoformat() if date_values else None,
                            "sort_keys": list(contract.sort_keys),
                        }
                    ],
                }
            )

        benchmark_unsigned = {
            "schema_version": 1,
            "benchmark_id": "test-csi300",
            "instrument_id": "sh000300",
            "display_name": "CSI 300",
            "currency": "CNY",
            "price_field": "close",
            "source": "test",
            "source_revision": "test-csi300-v1",
            "acquired_at": "2024-01-03T16:00:00+08:00",
            "observations": [
                {"date": "2024-01-02", "close": "100"},
                {"date": "2024-01-03", "close": "101"},
            ],
        }
        benchmark = {
            **benchmark_unsigned,
            "digest": self._json_content_digest(benchmark_unsigned),
        }
        industry_unsigned = {
            "schema_version": "1.0.0",
            "mapping_version": "test-industry-v1",
            "usage": "static_non_pit_concentration_only",
            "source": {
                "provider": "test",
                "endpoint": "test://industry",
                "retrieved_at": "2024-01-03T16:00:00+08:00",
                "source_revision": "test-industry-source-v1",
            },
            "memberships": [
                {
                    "security_id": security_id,
                    "stock_code": "600000",
                    "exchange": "SSE",
                    "level1_industry_id": "test-industry",
                }
            ],
        }
        industry = {
            **industry_unsigned,
            "artifact_digest": self._json_content_digest(industry_unsigned),
        }
        profile_values = []
        for name, payload, logical_digest in (
            ("benchmark_curve", benchmark, benchmark["digest"]),
            ("static_industry_mapping", industry, industry["artifact_digest"]),
        ):
            path = self.source / f"{name}.json"
            self._write_json(path, payload)
            profile_values.append(
                {
                    "name": name,
                    "role": self.policy.tables[name].role,
                    "logical_digest": logical_digest,
                    "source_lineage": {
                        "lineage_digest": SOURCE_LINEAGE,
                        "manifest_id": "synthetic-export-v1-manifest",
                        "knowledge_cutoff": "2024-01-03T18:00:00+08:00",
                        "source_revisions": {"source": f"test-{name}-v1"},
                    },
                    "source_manifest": {
                        "manifest_id": "synthetic-export-v1-manifest",
                        "knowledge_cutoff": "2024-01-03T18:00:00+08:00",
                        "source_revisions": {"source": f"test-{name}-v1"},
                    },
                    "parts": [
                        {
                            "path": path.name,
                            "sha256": _sha256(path),
                            "size_bytes": path.stat().st_size,
                        }
                    ],
                }
            )
        self._write_json(
            self.source / "manifest.json",
            {
                "schema_version": 1,
                "kind": "synthetic_semantic_export",
                "bundle_id": "synthetic-export-v1",
                "bundle_content_digest": SOURCE_CONTENT,
                "tables": table_values,
                "profile_tables": profile_values,
            },
        )
        (self.source / "READY").write_text(
            _sha256(self.source / "manifest.json") + "\n", encoding="ascii"
        )

    @staticmethod
    def _json_content_digest(value: object) -> str:
        payload = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    def _write_plan(self) -> None:
        manifest_path = self.source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sha256 = _sha256(manifest_path)
        (self.source / "READY").write_text(manifest_sha256 + "\n", encoding="ascii")
        table_by_name = {
            item["name"]: item for item in [*manifest["tables"], *manifest["profile_tables"]]
        }
        self._write_json(
            self.plan_path,
            {
                "schema_version": 1,
                "contract_id": self.policy.contract_id,
                "profile": self.policy.profile,
                "package": {
                    "phase": "provisional",
                    "package_id": "ads-base-test-v1",
                    "through_date": "2024-01-03",
                },
                "authority": dict(self.policy.authority),
                "ranges": {
                    "context_start": "2024-01-02",
                    "context_end": "2024-01-03",
                    "decision_start": "2024-01-02",
                    "decision_end": "2024-01-02",
                    "execution_start": "2024-01-03",
                    "execution_end": "2024-01-03",
                    "outcome_start": "2024-01-02",
                    "outcome_end": "2024-01-03",
                },
                "replay_contract": {
                    "universe_policy_version": "test-universe-v1",
                    "corporate_action_policy_version": (
                        "p1-corporate-action-normalization-20260729-v2"
                    ),
                },
                "inventory_complete": True,
                "upstreams": [
                    {
                        "role": "dwd_base",
                        "object_id": "synthetic-export-v1",
                        "manifest_path": str(manifest_path),
                        "manifest_sha256": manifest_sha256,
                        "content_digest": SOURCE_CONTENT,
                    },
                    {
                        "role": "canonical_catalog",
                        "object_id": "synthetic-export-v1",
                        "manifest_path": str(manifest_path),
                        "manifest_sha256": manifest_sha256,
                        "content_digest": SOURCE_CONTENT,
                    },
                    {
                        "role": "dws_base",
                        "object_id": "synthetic-export-v1",
                        "manifest_path": str(manifest_path),
                        "manifest_sha256": manifest_sha256,
                        "content_digest": SOURCE_CONTENT,
                    },
                ],
                "inventory": [
                    {
                        "object_id": "synthetic-export-v1",
                        "kind": "derived_offline_research_export",
                        "manifest_path": str(manifest_path),
                        "manifest_sha256": manifest_sha256,
                        "content_digest": SOURCE_CONTENT,
                        "classification": "reusable_verified_semantic_content",
                        "authority_state": "derived_non_authoritative_input",
                        "reuse_decision": "reuse",
                        "coverage": {
                            "date_start": "2024-01-02",
                            "date_end": "2024-01-03",
                        },
                        "upstream_generation_digest": SOURCE_GENERATION,
                        "reason": "synthetic fixture has exact source and logical content digests",
                    }
                ],
                "tables": [
                    {
                        "name": name,
                        "sources": [
                            {
                                "object_id": "synthetic-export-v1",
                                "source_table": name,
                                "source_role": table_by_name[name]["role"],
                                "table_content_digest": table_by_name[name].get(
                                    "table_content_digest",
                                    table_by_name[name].get("logical_digest"),
                                ),
                                "lineage_digest": SOURCE_LINEAGE,
                                "pit_evidence_digest": (
                                    PIT_EVIDENCE
                                    if self.policy.tables[name].require_per_decision_pit
                                    else None
                                ),
                            }
                        ],
                    }
                    for name in sorted(table_by_name)
                ],
                "generation_ledger": [
                    {
                        "market_date": market_date,
                        "source_generation_id": f"generation-{market_date}",
                        "source_lane": "cn01",
                        "knowledge_cutoff": f"{market_date}T21:00:00+00:00",
                        "decision_cutoff": f"{market_date}T22:00:00+00:00",
                        "catalog_manifest_sha256": manifest_sha256,
                        "dwd_manifest_sha256": manifest_sha256,
                        "dws_manifest_sha256": manifest_sha256,
                    }
                    for market_date in ("2024-01-02",)
                ],
                "absorbed_daily": [],
                "prior_base": None,
            },
        )

    def _plan(self):  # type: ignore[no-untyped-def]
        return load_operational_ads_base_plan(self.plan_path, policy=self.policy)

    def _build(self, parent: str = "build"):  # type: ignore[no-untyped-def]
        output = self.root / parent / "ads-base-test-v1"
        return build_operational_ads_base(
            plan=self._plan(),
            policy=self.policy,
            output=output,
            code_revision="a" * 40,
        )

    def test_generation_ledger_accepts_next_open_preopen_and_rejects_open(self) -> None:
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        value["generation_ledger"][0]["decision_cutoff"] = "2024-01-03T09:29:59+08:00"
        self._write_json(self.plan_path, value)
        self._plan()

        value["generation_ledger"][0]["decision_cutoff"] = "2024-01-03T09:30:00+08:00"
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "PIT or lineage"):
            self._plan()

    def test_generation_ledger_uses_next_open_across_a_weekend(self) -> None:
        ledger = [
            {
                "market_date": "2024-01-05",
                "decision_cutoff": "2024-01-08T09:29:59+08:00",
            }
        ]
        _validate_ledger_decision_calendar(
            ledger, (date(2024, 1, 5), date(2024, 1, 8))
        )
        ledger[0]["decision_cutoff"] = "2024-01-09T08:00:00+08:00"
        with self.assertRaisesRegex(OperationalADSBaseError, "next open session"):
            _validate_ledger_decision_calendar(
                ledger, (date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9))
            )

    def test_build_is_reproducible_and_verifies_without_source_paths(self) -> None:
        first = self._build("first")
        second = self._build("second")
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertEqual(first.plan_digest, second.plan_digest)
        source_manifest = json.loads((self.source / "manifest.json").read_text(encoding="utf-8"))
        source_tables = {
            item["name"]: item
            for item in [*source_manifest["tables"], *source_manifest["profile_tables"]]
        }
        first_manifest = json.loads((first.root / "manifest.json").read_text(encoding="utf-8"))
        for table in first_manifest["tables"]:
            source_table = source_tables[table["name"]]
            source_part = source_table["parts"][0]
            built_part = table["parts"][0]
            self.assertEqual(built_part["sha256"], source_part["sha256"])
            self.assertEqual(built_part["size_bytes"], source_part["size_bytes"])
            source_digest = source_table.get(
                "table_content_digest", source_table.get("logical_digest")
            )
            self.assertEqual(
                table["source_bindings"][0]["table_content_digest"],
                source_digest,
            )
            if self.policy.tables[table["name"]].format == "parquet":
                self.assertEqual(table["table_content_digest"], source_digest)
            else:
                self.assertNotEqual(table["table_content_digest"], source_digest)
        clean = self.root / "clean" / first.package_id
        clean.parent.mkdir()
        shutil.copytree(first.root, clean)
        unavailable = self.root / "source-unavailable"
        self.source.rename(unavailable)
        verified = verify_operational_ads_base(
            clean,
            policy=self.policy,
            verify_logical_content=True,
        )
        self.assertEqual(verified.content_digest, first.content_digest)

    def test_package_content_identity_excludes_publisher_and_part_encoding_metadata(self) -> None:
        first = {
            "contract_id": "operational-ads-base-test-v1",
            "publisher": {"code_revision": "a" * 40, "runtime": {"python_series": "3.11"}},
            "tables": [
                {
                    "name": "daily_ohlcv_none",
                    "table_content_digest": SOURCE_CONTENT,
                    "parts": [
                        {
                            "path": "tables/daily_ohlcv_none.parquet",
                            "sha256": f"sha256:{'a' * 64}",
                            "size_bytes": 100,
                            "row_count": 2,
                            "schema_sha256": f"sha256:{'b' * 64}",
                        }
                    ],
                }
            ],
        }
        reencoded = json.loads(json.dumps(first))
        reencoded["publisher"] = {
            "code_revision": "b" * 40,
            "runtime": {"python_series": "3.12"},
        }
        reencoded["tables"][0]["parts"][0]["sha256"] = f"sha256:{'c' * 64}"
        reencoded["tables"][0]["parts"][0]["size_bytes"] = 120

        self.assertEqual(
            _package_content_identity(first),
            _package_content_identity(reencoded),
        )
        reencoded["tables"][0]["parts"][0]["path"] = "tables/other.parquet"
        self.assertNotEqual(
            _package_content_identity(first),
            _package_content_identity(reencoded),
        )

    def test_publisher_revision_changes_manifest_but_not_logical_content(self) -> None:
        first = self._build("publisher-a")
        second = build_operational_ads_base(
            plan=self._plan(),
            policy=self.policy,
            output=self.root / "publisher-b" / "ads-base-test-v1",
            code_revision="b" * 40,
        )
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)

    def test_role_capabilities_isolate_decision_runner_and_outcome_tables(self) -> None:
        bundle = self._build()
        decision = open_operational_ads_base(
            bundle.root,
            policy=self.policy,
            reader_role="replay_decision_reader",
        )
        self.assertEqual(len(decision.table_paths("daily_ohlcv_none")), 1)
        with self.assertRaisesRegex(OperationalADSBaseError, "cannot access"):
            decision.table_paths("trading_calendar")
        with self.assertRaisesRegex(OperationalADSBaseError, "cannot access"):
            decision.table_paths("benchmark_curve")
        runner = open_operational_ads_base(
            bundle.root,
            policy=self.policy,
            reader_role="replay_runner",
        )
        self.assertEqual(len(runner.table_paths("trading_calendar")), 1)
        with self.assertRaisesRegex(OperationalADSBaseError, "cannot access"):
            runner.table_paths("benchmark_curve")
        evaluator = open_operational_ads_base(
            bundle.root,
            policy=self.policy,
            reader_role="replay_evaluator",
        )
        self.assertEqual(len(evaluator.table_paths("benchmark_curve")), 1)

    def test_promotion_is_immutable_and_current_is_compare_and_swap(self) -> None:
        bundle = self._build()
        base_root = self.root / "published"
        promoted = promote_operational_ads_base(
            source=bundle.root,
            base_root=base_root,
            policy=self.policy,
            expected_current_manifest_sha256="absent",
        )
        current = load_operational_ads_base_current(
            base_root / "BASE_CURRENT.json",
            policy=self.policy,
        )
        self.assertEqual(current.manifest_sha256, promoted.manifest_sha256)
        self.assertEqual((base_root / "BASE_CURRENT.json").stat().st_mode & 0o777, 0o640)
        descriptor = json.loads((base_root / "BASE_CURRENT.json").read_text(encoding="utf-8"))
        descriptor["manifest_sha256"] = f"sha256:{'9' * 64}"
        self._write_json(base_root / "BASE_CURRENT.json", descriptor)
        with self.assertRaisesRegex(OperationalADSBaseError, "compare-and-swap"):
            promote_operational_ads_base(
                source=bundle.root,
                base_root=base_root,
                policy=self.policy,
                expected_current_manifest_sha256="absent",
            )

    def test_tampered_part_is_rejected(self) -> None:
        bundle = self._build()
        part = bundle.root / bundle.table_parts["daily_ohlcv_none"][0]
        part.write_bytes(part.read_bytes() + b"tamper")
        with self.assertRaisesRegex(OperationalADSBaseError, "part integrity"):
            verify_operational_ads_base(bundle.root, policy=self.policy)

    def test_duplicate_source_key_blocks_build_and_exposes_no_ready(self) -> None:
        self._write_source(duplicate_daily_key=True)
        self._write_plan()
        output = self.root / "duplicate" / "ads-base-test-v1"
        with self.assertRaisesRegex(OperationalADSBaseError, "duplicate business keys"):
            build_operational_ads_base(
                plan=self._plan(),
                policy=self.policy,
                output=output,
                code_revision="a" * 40,
            )
        self.assertFalse(output.exists())

    def test_plan_rejects_multiple_sources_before_staging(self) -> None:
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        value["tables"][0]["sources"].append(dict(value["tables"][0]["sources"][0]))
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "exactly one sealed"):
            self._plan()
        self.assertFalse((self.root / "multi-source").exists())

    def test_tampered_source_part_is_rejected_before_publication(self) -> None:
        source_part = self.source / "daily_ohlcv_none.parquet"
        source_part.write_bytes(source_part.read_bytes() + b"tamper")
        self._write_plan()
        output = self.root / "source-tamper" / "ads-base-test-v1"
        with self.assertRaisesRegex(OperationalADSBaseError, "source table part integrity"):
            build_operational_ads_base(
                plan=self._plan(),
                policy=self.policy,
                output=output,
                code_revision="a" * 40,
            )
        self.assertFalse(output.exists())

    def test_interrupted_publish_exposes_no_target(self) -> None:
        output = self.root / "interrupted" / "ads-base-test-v1"
        with (
            mock.patch(
                "quanthus.operational_ads_base.os.replace",
                side_effect=RuntimeError("injected before publication"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected"),
        ):
            build_operational_ads_base(
                plan=self._plan(),
                policy=self.policy,
                output=output,
                code_revision="a" * 40,
            )
        self.assertFalse(output.exists())

    def test_plan_rejects_floating_pointer_and_missing_pit_evidence(self) -> None:
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        value["inventory"][0]["manifest_path"] = "/srv/example/CURRENT/manifest.json"
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "must not resolve CURRENT"):
            self._plan()
        self._write_plan()
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        daily = next(item for item in value["tables"] if item["name"] == "daily_ohlcv_none")
        daily["sources"][0]["pit_evidence_digest"] = None
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "lacks PIT evidence"):
            self._plan()

    def test_plan_cross_binds_upstream_inventory_and_generation_ledger(self) -> None:
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        value["upstreams"][0]["manifest_sha256"] = f"sha256:{'4' * 64}"
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "frozen inventory binding"):
            self._plan()

        self._write_plan()
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        value["generation_ledger"][0]["dwd_manifest_sha256"] = f"sha256:{'7' * 64}"
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "bound dwd_base"):
            self._plan()

    def test_upstream_descriptor_drift_blocks_preflight(self) -> None:
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        current_paths: dict[str, Path] = {}
        ledger_content = {
            "contract_id": "dwd-dws-base-test-v1",
            "phase": "provisional",
            "through_date": "2024-01-03",
            "generations": [],
        }
        ledger_digest = self._json_content_digest(ledger_content)
        ledger_value = {
            "schema_version": 1,
            "kind": "dwd_dws_base_input_ledger",
            **ledger_content,
            "ledger_digest": ledger_digest,
        }
        ledger_paths: dict[str, Path] = {}
        packages: dict[str, dict[str, str]] = {}
        for role, layer, digest_character in (
            ("dwd_base", "dwd", "6"),
            ("dws_base", "dws", "8"),
        ):
            package_id = f"{layer}-base-test-v1"
            package_root = self.root / f"{layer}-base" / package_id
            content_digest = f"sha256:{digest_character * 64}"
            manifest_path = package_root / "manifest.json"
            ledger_path = package_root / "lineage" / "input-ledger.json"
            self._write_json(ledger_path, ledger_value)
            ledger_sha256 = _sha256(ledger_path)
            self._write_json(
                manifest_path,
                {
                    "schema_version": 1,
                    "kind": "dwd_dws_monthly_base_package",
                    "layer": layer,
                    "package_id": package_id,
                    "manifest_id": f"{package_id}-manifest-v1",
                    "contract_id": ledger_content["contract_id"],
                    "phase": "provisional",
                    "content_digest": content_digest,
                    "through_date": "2024-01-03",
                    "input_ledger": {
                        "path": "lineage/input-ledger.json",
                        "sha256": ledger_sha256,
                        "digest": ledger_digest,
                    },
                    "files": [
                        {
                            "path": "lineage/input-ledger.json",
                            "sha256": ledger_sha256,
                            "size_bytes": ledger_path.stat().st_size,
                        }
                    ],
                },
            )
            manifest_sha256 = _sha256(manifest_path)
            (package_root / "READY").write_text(manifest_sha256 + "\n", encoding="ascii")
            upstream = next(item for item in value["upstreams"] if item["role"] == role)
            upstream.update(
                {
                    "object_id": package_id,
                    "manifest_path": str(manifest_path),
                    "manifest_sha256": manifest_sha256,
                    "content_digest": content_digest,
                }
            )
            inventory = dict(value["inventory"][0])
            inventory.update(
                {
                    "object_id": package_id,
                    "kind": "dwd_dws_monthly_base_package",
                    "manifest_path": str(manifest_path),
                    "manifest_sha256": manifest_sha256,
                    "content_digest": content_digest,
                }
            )
            value["inventory"].append(inventory)
            value["generation_ledger"][0][f"{layer}_manifest_sha256"] = manifest_sha256
            packages[layer] = {
                "package_id": package_id,
                "manifest_sha256": manifest_sha256,
                "content_digest": content_digest,
            }
            ledger_paths[layer] = ledger_path
        self._write_json(self.plan_path, value)
        plan = self._plan()
        pair_digest = self._json_content_digest(
            {
                "dwd_manifest_sha256": packages["dwd"]["manifest_sha256"],
                "dwd_content_digest": packages["dwd"]["content_digest"],
                "dws_manifest_sha256": packages["dws"]["manifest_sha256"],
                "dws_content_digest": packages["dws"]["content_digest"],
                "input_ledger_digest": ledger_digest,
            }
        )
        for layer, peer_layer in (("dwd", "dws"), ("dws", "dwd")):
            package = packages[layer]
            peer = packages[peer_layer]
            path = self.root / f"{layer}-base" / "BASE_CURRENT.json"
            self._write_json(
                path,
                {
                    "schema_version": 1,
                    "kind": "dwd_dws_base_current",
                    "layer": layer,
                    "package_id": package["package_id"],
                    "package_path": package["package_id"],
                    "manifest_sha256": package["manifest_sha256"],
                    "content_digest": package["content_digest"],
                    "through_date": plan.through_date.isoformat(),
                    "input_ledger_digest": ledger_digest,
                    "peer_package_id": peer["package_id"],
                    "peer_manifest_sha256": peer["manifest_sha256"],
                    "pair_digest": pair_digest,
                },
            )
            current_paths[layer] = path
        report = preflight_operational_ads_base_upstreams(
            plan,
            dwd_current=current_paths["dwd"],
            dws_current=current_paths["dws"],
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["pair_digest"], pair_digest)

        dwd_ledger = ledger_paths["dwd"]
        original_ledger = dwd_ledger.read_bytes()
        dwd_ledger.unlink()
        with self.assertRaisesRegex(OperationalADSBaseError, "input ledger path"):
            preflight_operational_ads_base_upstreams(
                plan,
                dwd_current=current_paths["dwd"],
                dws_current=current_paths["dws"],
            )
        dwd_ledger.write_bytes(original_ledger + b" ")
        with self.assertRaisesRegex(OperationalADSBaseError, "input ledger checksum"):
            preflight_operational_ads_base_upstreams(
                plan,
                dwd_current=current_paths["dwd"],
                dws_current=current_paths["dws"],
            )
        dwd_ledger.write_bytes(original_ledger)

        original = json.loads(current_paths["dwd"].read_text(encoding="utf-8"))
        mutations = {
            "missing schema": {
                key: value for key, value in original.items() if key != "schema_version"
            },
            "unsupported schema": {**original, "schema_version": 999},
            "wrong package path": {**original, "package_path": "different-package"},
            "wrong manifest": {**original, "manifest_sha256": f"sha256:{'9' * 64}"},
            "wrong pair digest": {**original, "pair_digest": f"sha256:{'9' * 64}"},
            "extra field": {**original, "unexpected": True},
        }
        for name, descriptor in mutations.items():
            with self.subTest(name=name):
                self._write_json(current_paths["dwd"], descriptor)
                with self.assertRaisesRegex(
                    OperationalADSBaseError, "unsupported|drifted|differs"
                ):
                    preflight_operational_ads_base_upstreams(
                        plan,
                        dwd_current=current_paths["dwd"],
                        dws_current=current_paths["dws"],
                    )
        self._write_json(current_paths["dwd"], original)

        dws_original = json.loads(current_paths["dws"].read_text(encoding="utf-8"))
        forged_pair_digest = f"sha256:{'9' * 64}"
        self._write_json(
            current_paths["dwd"], {**original, "pair_digest": forged_pair_digest}
        )
        self._write_json(
            current_paths["dws"], {**dws_original, "pair_digest": forged_pair_digest}
        )
        with self.assertRaisesRegex(OperationalADSBaseError, "differs"):
            preflight_operational_ads_base_upstreams(
                plan,
                dwd_current=current_paths["dwd"],
                dws_current=current_paths["dws"],
            )

    def test_upstream_input_ledger_rejects_path_and_semantic_forgery(self) -> None:
        package_root = self.root / "ledger-package"
        ledger_path = package_root / "lineage" / "input-ledger.json"
        content = {
            "contract_id": "dwd-dws-base-test-v1",
            "phase": "provisional",
            "through_date": "2024-01-03",
            "generations": [],
        }
        digest = self._json_content_digest(content)
        self._write_json(
            ledger_path,
            {
                "schema_version": 1,
                "kind": "dwd_dws_base_input_ledger",
                **content,
                "ledger_digest": digest,
            },
        )
        sha256 = _sha256(ledger_path)
        manifest = {
            "contract_id": content["contract_id"],
            "phase": "provisional",
            "input_ledger": {
                "path": "lineage/input-ledger.json",
                "sha256": sha256,
                "digest": digest,
            },
            "files": [
                {
                    "path": "lineage/input-ledger.json",
                    "sha256": sha256,
                    "size_bytes": ledger_path.stat().st_size,
                }
            ],
        }
        self.assertEqual(
            _verify_dwd_dws_input_ledger(
                manifest,
                package_root=package_root,
                layer="dwd",
                phase="provisional",
                through_date=date(2024, 1, 3),
            ),
            digest,
        )

        escaped = json.loads(json.dumps(manifest))
        escaped["input_ledger"]["path"] = "../input-ledger.json"
        with self.assertRaisesRegex(OperationalADSBaseError, "safe relative path"):
            _verify_dwd_dws_input_ledger(
                escaped,
                package_root=package_root,
                layer="dwd",
                phase="provisional",
                through_date=date(2024, 1, 3),
            )

        for alias in (
            "lineage/./input-ledger.json",
            "lineage//input-ledger.json",
        ):
            noncanonical = json.loads(json.dumps(manifest))
            noncanonical["input_ledger"]["path"] = alias
            with self.assertRaisesRegex(OperationalADSBaseError, "path is not canonical"):
                _verify_dwd_dws_input_ledger(
                    noncanonical,
                    package_root=package_root,
                    layer="dwd",
                    phase="provisional",
                    through_date=date(2024, 1, 3),
                )

        direct_link = package_root / "input-ledger-link.json"
        direct_link.symlink_to(ledger_path)
        direct_symlink = json.loads(json.dumps(manifest))
        direct_symlink["input_ledger"]["path"] = direct_link.name
        with self.assertRaisesRegex(OperationalADSBaseError, "path is not exact"):
            _verify_dwd_dws_input_ledger(
                direct_symlink,
                package_root=package_root,
                layer="dwd",
                phase="provisional",
                through_date=date(2024, 1, 3),
            )

        ancestor_link = package_root / "linked-lineage"
        ancestor_link.symlink_to(ledger_path.parent, target_is_directory=True)
        ancestor_symlink = json.loads(json.dumps(manifest))
        ancestor_symlink["input_ledger"]["path"] = "linked-lineage/input-ledger.json"
        with self.assertRaisesRegex(OperationalADSBaseError, "path is not exact"):
            _verify_dwd_dws_input_ledger(
                ancestor_symlink,
                package_root=package_root,
                layer="dwd",
                phase="provisional",
                through_date=date(2024, 1, 3),
            )

        forged = json.loads(ledger_path.read_text(encoding="utf-8"))
        forged["generations"] = [{"market_date": "2024-01-03"}]
        self._write_json(ledger_path, forged)
        forged_sha256 = _sha256(ledger_path)
        manifest["input_ledger"]["sha256"] = forged_sha256
        manifest["files"][0].update(
            {"sha256": forged_sha256, "size_bytes": ledger_path.stat().st_size}
        )
        with self.assertRaisesRegex(OperationalADSBaseError, "input ledger content"):
            _verify_dwd_dws_input_ledger(
                manifest,
                package_root=package_root,
                layer="dwd",
                phase="provisional",
                through_date=date(2024, 1, 3),
            )

    def test_build_rejects_manifest_logical_identity_mismatch(self) -> None:
        manifest_path = self.source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["bundle_id"] = "different-export-v1"
        self._write_json(manifest_path, manifest)
        self._write_plan()
        output = self.root / "identity" / "ads-base-test-v1"
        with self.assertRaisesRegex(OperationalADSBaseError, "logical id"):
            build_operational_ads_base(
                plan=self._plan(),
                policy=self.policy,
                output=output,
                code_revision="a" * 40,
            )
        self.assertFalse(output.exists())

    def test_build_accepts_exact_file_identity_for_digestless_catalog(self) -> None:
        catalog_root = self.root / "catalog-without-embedded-content"
        catalog_path = catalog_root / "catalog.json"
        self._write_json(
            catalog_path,
            {
                "schema_version": 1,
                "kind": "canonical_catalog_snapshot",
                "catalog_id": catalog_root.name,
                "market_date_generations": {
                    "2024-01-02": "generation-2024-01-02",
                },
            },
        )
        catalog_sha256 = _sha256(catalog_path)
        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        catalog_upstream = next(
            item for item in value["upstreams"] if item["role"] == "canonical_catalog"
        )
        catalog_upstream.update(
            {
                "object_id": catalog_root.name,
                "manifest_path": str(catalog_path),
                "manifest_sha256": catalog_sha256,
                "content_digest": catalog_sha256,
            }
        )
        source_inventory = dict(value["inventory"][0])
        source_inventory.update(
            {
                "object_id": catalog_root.name,
                "kind": "canonical_catalog_snapshot",
                "manifest_path": str(catalog_path),
                "manifest_sha256": catalog_sha256,
                "content_digest": catalog_sha256,
                "classification": "reusable_verified_semantic_content",
                "authority_state": "canonical_accepted",
                "reuse_decision": "evidence_only",
                "reason": "catalog file SHA is its exact identity when no digest is embedded",
            }
        )
        value["inventory"].append(source_inventory)
        value["generation_ledger"][0]["catalog_manifest_sha256"] = catalog_sha256
        self._write_json(self.plan_path, value)

        bundle = self._build("digestless-catalog")
        self.assertTrue((bundle.root / "READY").is_file())

        value = json.loads(self.plan_path.read_text(encoding="utf-8"))
        catalog_upstream = next(
            item for item in value["upstreams"] if item["role"] == "canonical_catalog"
        )
        catalog_upstream["content_digest"] = SOURCE_CONTENT
        inventory = next(
            item for item in value["inventory"] if item["object_id"] == catalog_root.name
        )
        inventory["content_digest"] = SOURCE_CONTENT
        self._write_json(self.plan_path, value)
        with self.assertRaisesRegex(OperationalADSBaseError, "content digest"):
            self._build("wrong-digestless-catalog")

    def test_source_path_traversal_is_rejected_before_staging(self) -> None:
        manifest_path = self.source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tables"][0]["parts"][0]["path"] = "../daily.parquet"
        self._write_json(manifest_path, manifest)
        self._write_plan()
        output = self.root / "unsafe" / "ads-base-test-v1"
        with self.assertRaisesRegex(OperationalADSBaseError, "safe relative path"):
            build_operational_ads_base(
                plan=self._plan(),
                policy=self.policy,
                output=output,
                code_revision="a" * 40,
            )
        self.assertFalse(output.exists())

    def test_clean_copied_base_runs_bounded_existing_reader_smoke(self) -> None:
        bundle = self._build()
        clean = self.root / "smoke" / bundle.package_id
        clean.parent.mkdir()
        shutil.copytree(bundle.root, clean)
        self.source.rename(self.root / "unavailable-after-build")
        reader = load_operational_ads_replay_reader(
            clean,
            policy=self.policy,
            corporate_action_policy=load_corporate_action_normalization_policy(),
        )
        self.assertEqual(len(reader.frames(date(2024, 1, 2), date(2024, 1, 2))), 1)
        report = smoke_operational_ads_replay(
            clean,
            policy=self.policy,
            corporate_action_policy=load_corporate_action_normalization_policy(),
            scope_start=date(2024, 1, 2),
            scope_end=date(2024, 1, 2),
        )
        self.assertTrue(report["frame_audit"]["ready_for_strategy_replay"])
        self.assertFalse(report["execution_authorized"])

    def test_replay_and_promotion_do_not_rehash_logical_rows(self) -> None:
        bundle = self._build()
        with mock.patch(
            "quanthus.operational_ads_base._parquet_content_digest",
            side_effect=AssertionError("logical row hashing must be explicit"),
        ):
            load_operational_ads_replay_reader(
                bundle.root,
                policy=self.policy,
                corporate_action_policy=load_corporate_action_normalization_policy(),
            )
            promoted = promote_operational_ads_base(
                source=bundle.root,
                base_root=self.root / "fast-promotion",
                policy=self.policy,
                expected_current_manifest_sha256="absent",
            )
        self.assertEqual(promoted.content_digest, bundle.content_digest)
