from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.backtest.inputs import CoverageStatus, P1DailyExecutionEvidence
from quanthus.contracts import SnapshotRef
from quanthus.event_library.physical_scan import (
    PhysicalEventScanError,
    _audit_projection_static_industry_coverage,
    _candidate_input,
    _contiguous_weight_buckets,
    _publish_package,
    iter_projected_candidate_series,
    materialize_p4_research_adm,
    scan_event_candidate_projection,
    verify_physical_event_scan,
)
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.policy import find_project_root

ROOT = find_project_root()
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def _digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _weekdays(start: date, count: int) -> list[date]:
    values: list[date] = []
    cursor = start
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return values


class _Bundle:
    def __init__(self, paths: dict[str, Path]) -> None:
        self.paths = paths

    def partition_paths(self, dataset: str) -> tuple[Path, ...]:
        return (self.paths[dataset],)


class _Reader:
    def __init__(self, paths: dict[str, Path]) -> None:
        self.bundle = _Bundle(paths)
        self.snapshot_id = "synthetic-direct-dwd-execution-source-v1"

    def execution_evidence_batch(
        self, requests: tuple[tuple[str, date], ...]
    ) -> dict[tuple[str, date], P1DailyExecutionEvidence]:
        result: dict[tuple[str, date], P1DailyExecutionEvidence] = {}
        for security_code, execution_date in requests:
            close_at = datetime.combine(execution_date, time(15, 0), SHANGHAI)
            result[(security_code, execution_date)] = P1DailyExecutionEvidence(
                snapshot=SnapshotRef(
                    market_date=execution_date,
                    knowledge_cutoff=close_at,
                    source_revision="synthetic-direct-dwd-v1",
                    snapshot_id=self.snapshot_id,
                ),
                security_code=security_code,
                open_at=datetime.combine(execution_date, time(9, 30), SHANGHAI),
                close_at=close_at,
                coverage_status=CoverageStatus.TRADING,
                data_valid=True,
                is_executable=True,
                entry_policy_allows=True,
                reason_code="TRADING",
                open_price=Decimal("100.00"),
                high_price=Decimal("101.00"),
                low_price=Decimal("99.00"),
                close_price=Decimal("100.00"),
                upper_limit_price=Decimal("110.00"),
                lower_limit_price=Decimal("90.00"),
            )
        return result


class EventPhysicalScanTests(unittest.TestCase):
    def test_candidate_buckets_are_contiguous_and_row_balanced(self) -> None:
        weighted = (("a", 4), ("b", 3), ("c", 2), ("d", 1))

        self.assertEqual(
            _contiguous_weight_buckets(weighted, 2),
            (("a",), ("b", "c", "d")),
        )
        self.assertEqual(
            _contiguous_weight_buckets(weighted, 4),
            (("a",), ("b",), ("c",), ("d",)),
        )
        five_way = (("a", 5), ("b", 4), ("c", 3), ("d", 2), ("e", 1))
        self.assertEqual(
            _contiguous_weight_buckets(five_way, 5),
            (("a",), ("b",), ("c",), ("d",), ("e",)),
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = load_event_scan_source_policy(
            ROOT / "config/p4-event-scan-source.research.v1.json",
            release_path=ROOT / "config/releases/v2-build202607.json",
            provider_path=ROOT / "config/replay-data-provider.development.v2.json",
        )
        self.card = load_candidate_rule_card(
            ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
        )
        self.split = load_split_policy(
            ROOT / "config/p4-event-split-policy.research.v1.json"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_inputs(self) -> tuple[_Reader, Path]:
        history_dates = _weekdays(date(2011, 6, 1), 130)
        development_dates = _weekdays(date(2012, 1, 2), 60)
        all_dates = history_dates + development_dates
        signal_index = 43
        bars: list[dict[str, object]] = []
        universe: list[dict[str, object]] = []
        limits: list[dict[str, object]] = []
        calendar: list[dict[str, object]] = [
            {"exchange": "SSE", "trade_date": market_date, "is_open": 1}
            for market_date in _weekdays(date(2012, 1, 2), 2600)
            if market_date <= date(2021, 12, 31)
        ]
        security_effective = datetime(2010, 1, 1, tzinfo=UTC)
        listing_effective = datetime(2010, 1, 1, tzinfo=UTC)
        raw_knowledge = datetime(2026, 8, 3, 23, 59, tzinfo=UTC)
        for index, market_date in enumerate(all_dates):
            development_index = index - len(history_dates)
            range_fraction = Decimal("0.01")
            turnover = Decimal("100.00")
            if development_index == signal_index - 1:
                range_fraction = Decimal("0.005")
                turnover = Decimal("50.00")
            elif development_index == signal_index:
                range_fraction = Decimal("0.02")
                turnover = Decimal("200.00")
            open_price = Decimal("100.0000000000")
            bars.append(
                {
                    "security_id": "600001",
                    "date": market_date,
                    "dwd_open_decimal": open_price,
                    "dwd_high_decimal": open_price * (Decimal("1") + range_fraction),
                    "dwd_low_decimal": open_price,
                    "dwd_close_decimal": open_price,
                    "dwd_turnover_decimal": turnover,
                    "dwd_bar_knowledge_from": raw_knowledge,
                    "dwd_bar_source_record_id": f"bar-{market_date.isoformat()}",
                    "dwd_bar_source_revision": "bar-v1",
                    "dwd_bar_source_artifact_sha256": "sha256:" + "a" * 64,
                }
            )
            universe.append(
                {
                    "security_id": "600001",
                    "stock_code": "600001",
                    "dwd_security_id": "opaque-security-001",
                    "dwd_company_id": "company-001",
                    "dwd_listing_id": "listing-001",
                    "dwd_venue_id": "venue-sse",
                    "dwd_instrument_type": "common_equity",
                    "dwd_security_effective_from": security_effective,
                    "dwd_listing_effective_from": listing_effective,
                    "dwd_security_knowledge_from": raw_knowledge,
                    "dwd_listing_knowledge_from": raw_knowledge,
                    "dwd_state_knowledge_from": raw_knowledge,
                    "exchange": "SSE",
                    "market_date": market_date,
                }
            )
            limits.append(
                {
                    "security_id": "600001",
                    "execution_date": market_date,
                    "observed_tradestatus": "1",
                    "dwd_state_knowledge_from": raw_knowledge,
                }
            )
        paths: dict[str, Path] = {}
        for dataset, rows in (
            ("daily_ohlcv_none", bars),
            ("daily_universe", universe),
            ("price_limit_execution_daily", limits),
            ("trading_calendar", calendar),
        ):
            path = self.root / f"{dataset}.parquet"
            pq.write_table(pa.Table.from_pylist(rows), path)
            paths[dataset] = path

        dwd_root = self.root / "dwd-base-202607-v2.1"
        factor_path = dwd_root / "payload/disclosure_company_event/dwd_adjustment_factor.parquet"
        factor_path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "other-security",
                        "effective_from": datetime(2013, 1, 1, tzinfo=UTC),
                        "knowledge_from": raw_knowledge,
                        "knowledge_to": None,
                    }
                ]
            ),
            factor_path,
        )
        node_path = dwd_root / "payload/industry_classification/dwd_industry_node.parquet"
        node_path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "industry_node_id": "industry-level1",
                        "parent_node_id": None,
                        "node_code": "A",
                        "node_level": 1,
                        "knowledge_to": None,
                    },
                    {
                        "industry_node_id": "industry-leaf",
                        "parent_node_id": "industry-level1",
                        "node_code": "A01",
                        "node_level": 2,
                        "knowledge_to": None,
                    },
                ]
            ),
            node_path,
        )
        membership_path = (
            dwd_root
            / "payload/industry_classification/dwd_company_industry_membership.parquet"
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "company_id": "company-001",
                        "industry_node_id": "industry-leaf",
                        "membership_type": "PRIMARY_CLASSIFICATION",
                        "knowledge_to": None,
                    }
                ]
            ),
            membership_path,
        )
        listing_path = dwd_root / "payload/listed_company/dwd_listing.parquet"
        listing_path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "opaque-security-001",
                        "knowledge_to": None,
                        "effective_to": None,
                    }
                ]
            ),
            listing_path,
        )
        manifest_files = []
        for path in (factor_path, node_path, membership_path, listing_path):
            manifest_files.append(
                {
                    "path": path.relative_to(dwd_root).as_posix(),
                    "sha256": "sha256:" + "b" * 64,
                    "size_bytes": path.stat().st_size,
                }
            )
        (dwd_root / "manifest.json").write_text(
            json.dumps({"files": manifest_files}),
            encoding="utf-8",
        )
        return _Reader(paths), dwd_root

    def test_materialized_adm_streams_into_real_scan_contract(self) -> None:
        reader, dwd_root = self._write_inputs()
        projection = materialize_p4_research_adm(
            reader=reader,  # type: ignore[arg-type]
            dwd_root=dwd_root,
            source_policy=self.source,
            provider_identity={
                "provider_id": "synthetic-direct-dwd",
                "provider_version": "1.0.0",
                "provider_config_digest": "sha256:" + "c" * 64,
                "provider_lineage_digest": "sha256:" + "d" * 64,
            },
            output_root=self.root / "adm",
        )
        adm_manifest = json.loads(projection.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(adm_manifest["schema_version"], "quanthus.p4-research-adm.v1")
        self.assertEqual(adm_manifest["logical_layer"], "research_adm")
        self.assertEqual(adm_manifest["adm_policy"]["adm_id"], "p4-event-research-adm")
        industry = projection.industry_mapping
        self.assertEqual(
            set(industry.by_security_id),
            {"opaque-security-001"},
        )
        self.assertEqual(
            industry.by_security_id["opaque-security-001"].level1_industry_id,
            "industry-level1",
        )

        series = tuple(
            iter_projected_candidate_series(
                projection,
                source_policy=self.source,
                industry_mapping=industry,
                fetch_rows=7,
            )
        )
        connection = duckdb.connect(":memory:")
        try:
            cursor = connection.execute(
                "SELECT * FROM read_parquet(?) ORDER BY session_date LIMIT 1",
                [str(projection.candidate_path)],
            )
            names = [str(item[0]) for item in cursor.description]
            values = cursor.fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(values)
        assert values is not None
        legacy_row = _candidate_input(
            dict(zip(names, values, strict=True)),
            source_policy=self.source,
            industry_mapping=industry,
        )
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0][0], legacy_row)
        self.assertTrue(all(row.security_id == "opaque-security-001" for row in series[0]))
        self.assertTrue(all(row.knowledge_cutoff <= row.close_at for row in series[0]))
        selected_series = tuple(
            iter_projected_candidate_series(
                projection,
                source_policy=self.source,
                industry_mapping=industry,
                fetch_rows=7,
                allowed_security_ids=("opaque-security-001",),
            )
        )
        self.assertEqual(selected_series, series)

        duplicated_table = pa.concat_tables(
            [
                candidate_table := pq.read_table(projection.candidate_path),
                candidate_table.set_column(
                    candidate_table.schema.get_field_index("dwd_security_id"),
                    "dwd_security_id",
                    pa.array(["opaque-security-002"] * candidate_table.num_rows),
                ),
            ]
        )
        contiguous_path = self.root / "candidate-contiguous.parquet"
        pq.write_table(duplicated_table, contiguous_path)
        contiguous_projection = replace(projection, candidate_path=contiguous_path)
        selected_industry = replace(
            industry,
            memberships=industry.memberships
            + (
                replace(
                    industry.memberships[0], security_code="opaque-security-002"
                ),
            ),
        )
        contiguous_series = tuple(
            iter_projected_candidate_series(
                contiguous_projection,
                source_policy=self.source,
                industry_mapping=selected_industry,
                fetch_rows=7,
                allowed_security_ids=("opaque-security-002", "opaque-security-001"),
            )
        )
        self.assertEqual(
            [item[0].security_id for item in contiguous_series],
            ["opaque-security-001", "opaque-security-002"],
        )
        interleaved_path = self.root / "candidate-interleaved.parquet"
        pq.write_table(
            duplicated_table.take(
                pa.array(
                    [
                        index
                        for offset in range(candidate_table.num_rows)
                        for index in (offset, offset + candidate_table.num_rows)
                    ],
                    type=pa.int64(),
                )
            ),
            interleaved_path,
        )
        selected_projection = replace(projection, candidate_path=interleaved_path)
        with self.assertRaisesRegex(
            PhysicalEventScanError, "projected candidate physical order differs"
        ):
            tuple(
                iter_projected_candidate_series(
                    selected_projection,
                    source_policy=self.source,
                    industry_mapping=selected_industry,
                    fetch_rows=7,
                    allowed_security_ids=("opaque-security-002", "opaque-security-001"),
                )
            )

        reversed_path = self.root / "candidate-reversed.parquet"
        pq.write_table(
            candidate_table.take(
                pa.array(list(reversed(range(candidate_table.num_rows))), type=pa.int64())
            ),
            reversed_path,
        )
        reversed_projection = replace(projection, candidate_path=reversed_path)
        with self.assertRaisesRegex(
            PhysicalEventScanError, "projected candidate physical order differs"
        ):
            tuple(
                iter_projected_candidate_series(
                    reversed_projection,
                    source_policy=self.source,
                    industry_mapping=industry,
                    fetch_rows=7,
                )
            )

        scan, entries = scan_event_candidate_projection(
            projection,
            reader=reader,  # type: ignore[arg-type]
            industry_mapping=industry,
            source_policy=self.source,
            card=self.card,
            split_policy=self.split,
        )

        self.assertEqual(scan.counters.raw_triggers, 1)
        self.assertEqual(scan.counters.candidate_events, 1, scan.counters.to_dict())
        self.assertEqual(entries.executable_events, 1)
        self.assertEqual(entries.not_executable_events, 0)
        self.assertEqual(scan.candidates[0].record.identity.security_id, "opaque-security-001")
        self.assertEqual(
            entries.records[0].entry_source_snapshot_id,
            "synthetic-direct-dwd-execution-source-v1",
        )
        self.assertNotEqual(
            entries.records[0].entry_source_snapshot_id,
            scan.candidates[0].record.identity.snapshot_id,
        )

    def test_event_package_binds_research_adm(self) -> None:
        reader, dwd_root = self._write_inputs()
        staging = self.root / "event-staging"
        staging.mkdir()
        adm = materialize_p4_research_adm(
            reader=reader,  # type: ignore[arg-type]
            dwd_root=dwd_root,
            source_policy=self.source,
            provider_identity={
                "provider_id": "synthetic-direct-dwd",
                "provider_version": "1.0.0",
                "provider_config_digest": "sha256:" + "c" * 64,
                "provider_lineage_digest": "sha256:" + "d" * 64,
            },
            output_root=staging / "adm",
        )
        scan, entries = scan_event_candidate_projection(
            adm,
            reader=reader,  # type: ignore[arg-type]
            industry_mapping=adm.industry_mapping,
            source_policy=self.source,
            card=self.card,
            split_policy=self.split,
        )
        result = _publish_package(
            staging,
            output=self.root / "event-library",
            source_policy=self.source,
            card=self.card,
            split_policy=self.split,
            projection=adm,
            scan=scan,
            entries=entries,
            code_revision="synthetic-adm-test",
        )

        verified = verify_physical_event_scan(result.root)
        manifest = json.loads((result.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "quanthus.p4-event-library.v2")
        self.assertEqual(manifest["adm"]["id"], "p4-event-research-adm")
        self.assertTrue((result.root / "adm" / "adm.json").is_file())
        self.assertEqual(verified.content_digest, result.content_digest)

    def test_static_industry_coverage_audit_classifies_current_and_historical_gaps(self) -> None:
        candidate_path = self.root / "candidate-audit.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "dwd_security_id": "security-covered",
                        "dwd_company_id": "company-covered",
                        "listing_code": "600001",
                    },
                    {
                        "dwd_security_id": "security-current-gap",
                        "dwd_company_id": "company-current-gap",
                        "listing_code": "600002",
                    },
                    {
                        "dwd_security_id": "security-historical-gap",
                        "dwd_company_id": "company-historical-gap",
                        "listing_code": "600003",
                    },
                ]
            ),
            candidate_path,
        )
        dwd_root = self.root / "dwd-audit"
        membership_path = (
            dwd_root
            / "payload/industry_classification/dwd_company_industry_membership.parquet"
        )
        membership_path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "company_id": "company-covered",
                        "industry_node_id": "industry-leaf",
                        "membership_type": "PRIMARY_CLASSIFICATION",
                        "knowledge_to": None,
                    }
                ]
            ),
            membership_path,
        )
        node_path = dwd_root / "payload/industry_classification/dwd_industry_node.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "industry_node_id": "industry-level1",
                        "parent_node_id": None,
                        "node_code": "A",
                        "node_level": 1,
                        "knowledge_to": None,
                    },
                    {
                        "industry_node_id": "industry-leaf",
                        "parent_node_id": "industry-level1",
                        "node_code": "A01",
                        "node_level": 2,
                        "knowledge_to": None,
                    },
                ]
            ),
            node_path,
        )
        listing_path = dwd_root / "payload/listed_company/dwd_listing.parquet"
        listing_path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "security-covered",
                        "knowledge_to": None,
                        "effective_to": None,
                    },
                    {
                        "security_id": "security-current-gap",
                        "knowledge_to": None,
                        "effective_to": None,
                    },
                    {
                        "security_id": "security-historical-gap",
                        "knowledge_to": None,
                        "effective_to": datetime(2020, 1, 1, tzinfo=UTC),
                    },
                ]
            ),
            listing_path,
        )
        files = []
        for path in (membership_path, node_path, listing_path):
            files.append(
                {
                    "path": path.relative_to(dwd_root).as_posix(),
                    "sha256": "sha256:" + "f" * 64,
                    "size_bytes": path.stat().st_size,
                }
            )
        (dwd_root / "manifest.json").write_text(
            json.dumps({"files": files}), encoding="utf-8"
        )
        unsigned = {
            "schema_version": "1.0.0",
            "mapping_version": "static-level1-concentration-research-v1",
            "usage": "static_non_pit_concentration_only",
            "source": {
                "provider": "synthetic",
                "endpoint": "fixture://coverage-audit",
                "retrieved_at": "2026-08-20T00:00:00+00:00",
                "source_revision": "synthetic-v1",
            },
            "memberships": [
                {
                    "security_id": "security-covered",
                    "stock_code": "600001",
                    "exchange": "SSE",
                    "level1_industry_id": "industry-level1",
                }
            ],
        }
        mapping_path = self.root / "audit-industry.json"
        mapping_path.write_text(
            json.dumps({**unsigned, "artifact_digest": _digest(unsigned)}),
            encoding="utf-8",
        )
        mapping = load_static_industry_mapping(
            mapping_path,
            expected_version="static-level1-concentration-research-v1",
        )

        audit = _audit_projection_static_industry_coverage(
            dwd_root=dwd_root,
            candidate_path=candidate_path,
            industry_mapping=mapping,
            output_path=self.root / "coverage-audit.json",
        )

        self.assertEqual(audit["candidate_security_count"], 3)
        self.assertEqual(audit["mapped_security_count"], 1)
        self.assertEqual(audit["missing_security_count"], 2)
        self.assertEqual(audit["missing_current_listed"], 1)
        self.assertEqual(audit["missing_historical_only"], 1)
        self.assertEqual(
            audit["missing_reason_counts"], {"no_company_industry_membership": 2}
        )
        self.assertEqual(len(audit["missing_securities"]), 2)

    def test_physical_scan_fails_when_static_industry_does_not_cover_trigger(self) -> None:
        reader, dwd_root = self._write_inputs()
        projection = materialize_p4_research_adm(
            reader=reader,  # type: ignore[arg-type]
            dwd_root=dwd_root,
            source_policy=self.source,
            provider_identity={
                "provider_id": "synthetic-direct-dwd",
                "provider_version": "1.0.0",
                "provider_config_digest": "sha256:" + "c" * 64,
                "provider_lineage_digest": "sha256:" + "d" * 64,
            },
            output_root=self.root / "adm-missing-industry",
        )
        unsigned = {
            "schema_version": "1.0.0",
            "mapping_version": "static-level1-concentration-research-v1",
            "usage": "static_non_pit_concentration_only",
            "source": {
                "provider": "synthetic",
                "endpoint": "fixture://missing-industry",
                "retrieved_at": "2026-08-19T00:00:00+00:00",
                "source_revision": "synthetic-v1",
            },
            "memberships": [
                {
                    "security_id": "unrelated-security",
                    "stock_code": "000001",
                    "exchange": "SZSE",
                    "level1_industry_id": "industry-other",
                }
            ],
        }
        path = self.root / "missing-industry.json"
        path.write_text(
            json.dumps({**unsigned, "artifact_digest": _digest(unsigned)}),
            encoding="utf-8",
        )
        missing = load_static_industry_mapping(
            path,
            expected_version="static-level1-concentration-research-v1",
        )

        with self.assertRaisesRegex(
            PhysicalEventScanError,
            "does not cover all isolated candidates: missing=1",
        ):
            scan_event_candidate_projection(
                projection,
                reader=reader,  # type: ignore[arg-type]
                industry_mapping=missing,
                source_policy=self.source,
                card=self.card,
                split_policy=self.split,
            )


if __name__ == "__main__":
    unittest.main()
