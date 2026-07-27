import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_V2 = ROOT / "config" / "price-limit.v2.json"

P1_STACK_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)

if P1_STACK_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.price_limits import (
        PriceLimitBuildError,
        build_price_limit_revision,
        resolve_price_limit_policy,
        round_limit_price,
    )


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class PriceLimitBuildTests(unittest.TestCase):
    def test_rounding_is_exchange_style_half_up(self) -> None:
        self.assertEqual(
            round_limit_price(Decimal("10.05"), Decimal("10"), upper=True), Decimal("11.06")
        )
        self.assertEqual(
            round_limit_price(Decimal("10.05"), Decimal("10"), upper=False), Decimal("9.05")
        )

    def test_versioned_golden_cases(self) -> None:
        policy = json.loads(POLICY_V2.read_text(encoding="utf-8"))
        fixture_root = Path(__file__).parents[1] / "fixtures/golden"
        fixtures = (
            fixture_root / "price_limit_cases.v1.json",
            fixture_root / "price_limit_cases.v2.json",
        )
        for fixture in fixtures:
            cases = json.loads(fixture.read_text(encoding="utf-8"))
            if fixture.name.endswith("v1.json"):
                self.assertGreaterEqual(len(cases), 20)
            for case in cases:
                with self.subTest(fixture=fixture.name, case=case["id"]):
                    inputs = dict(case["inputs"])
                    if inputs["prior_trading_close"] is not None:
                        inputs["prior_trading_close"] = Decimal(
                            str(inputs["prior_trading_close"])
                        )
                    decision = resolve_price_limit_policy(**inputs, policy=policy)
                    expected = case["expected"]
                    self.assertEqual(
                        decision.selected_rule_basis, expected["selected_rule_basis"]
                    )
                    actual_pct = (
                        float(decision.upper_limit_pct)
                        if decision.upper_limit_pct is not None
                        else None
                    )
                    self.assertEqual(actual_pct, expected["upper_limit_pct"])
                    self.assertEqual(
                        decision.entry_policy_allows, expected["entry_policy_allows"]
                    )
                    self.assertEqual(decision.is_executable, expected["is_executable"])
                    self.assertEqual(decision.reason_code, expected["reason_code"])

    def test_preserved_v1_policy_remains_replayable(self) -> None:
        policy = json.loads((ROOT / "config" / "price-limit.v1.json").read_text(encoding="utf-8"))

        decision = resolve_price_limit_policy(
            board="SSE_main",
            market_date="2026-07-06",
            status_known=True,
            is_st_class=True,
            listing_window_rule=None,
            eligible_session_index=100,
            ipo_window_sessions=0,
            observed_tradestatus="1",
            prior_trading_close=Decimal("10"),
            policy=policy,
        )

        self.assertEqual(decision.upper_limit_pct, Decimal("5.0"))

    def test_policy_gap_is_rejected(self) -> None:
        policy = json.loads(POLICY_V2.read_text(encoding="utf-8"))
        sse = [row for row in policy["board_regimes"] if row["board"] == "SSE_main"]
        sse[1]["effective_start"] = "2026-07-07"

        with self.assertRaisesRegex(PriceLimitBuildError, "gap"):
            resolve_price_limit_policy(
                board="SSE_main",
                market_date="2026-07-06",
                status_known=True,
                is_st_class=True,
                listing_window_rule=None,
                eligible_session_index=100,
                ipo_window_sessions=0,
                observed_tradestatus="1",
                prior_trading_close=Decimal("10"),
                policy=policy,
            )

    def test_policy_percentage_above_one_hundred_is_rejected(self) -> None:
        policy = json.loads(POLICY_V2.read_text(encoding="utf-8"))
        policy["board_regimes"][0]["st_pct"] = 101

        with self.assertRaisesRegex(PriceLimitBuildError, "between 0 and 100"):
            resolve_price_limit_policy(
                board="SSE_main",
                market_date="2026-07-03",
                status_known=True,
                is_st_class=True,
                listing_window_rule=None,
                eligible_session_index=100,
                ipo_window_sessions=0,
                observed_tradestatus="1",
                prior_trading_close=Decimal("10"),
                policy=policy,
            )

    def test_real_sessions_drive_ipo_window_and_st_overrides_board(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / "identity"
            identity.mkdir()
            s2 = root / "s2"
            s2.mkdir()
            rules_dir = root / "rules"
            rules_dir.mkdir()
            sessions = [date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 8)]
            universe_rows = [
                {
                    "security_id": "SSE-688001",
                    "stock_code": "688001",
                    "exchange": "SSE",
                    "market_date": day,
                }
                for day in sessions
            ]
            pq.write_table(pa.Table.from_pylist(universe_rows), identity / "daily_universe.parquet")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-688001",
                            "stock_code": "688001",
                            "exchange": "SSE",
                            "date": day,
                            "tradestatus": status,
                            "raw_shard": "raw/688001.json",
                        }
                        for day, status in zip(sessions, ("1", "0", "1"), strict=True)
                    ]
                ),
                identity / "daily_isST.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-688001",
                            "stock_code": "688001",
                            "date": day,
                            "close": close,
                        }
                        for day, close in zip(sessions, (10.0, 10.0, 11.0), strict=True)
                    ]
                ),
                identity / "daily_ohlcv_none.parquet",
            )
            (identity / "META.json").write_text("{}\n", encoding="utf-8")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-688001",
                            "execution_date": day,
                            "status_label": "unknown" if index == 0 else "st_class",
                            "is_st_class": None if index == 0 else True,
                            "status_known": index > 0,
                            "source_market_date": None if index == 0 else sessions[index - 1],
                            "revision": "s2-v1",
                        }
                        for index, day in enumerate(sessions)
                    ]
                ),
                s2 / "st_status_execution_daily.parquet",
            )
            (s2 / "manifest.json").write_text("{}\n", encoding="utf-8")
            rule = {
                "interval_id": "ipo-1",
                "stock_code": "688001",
                "exchange": "SSE",
                "board": "STAR",
                "effective_start": sessions[0],
                "effective_end": None,
                "upper_limit_pct": None,
                "lower_limit_pct": None,
                "listing_window_rule": "ipo_first_5_trading_days_no_limit",
                "no_limit_days": 5.0,
                "price_tick": 0.01,
                "rounding_rule": "round_to_tick_0.01_half_up_exchange_style",
                "rule_basis": "IPO_window",
                "priority": 40,
                "announcement_date": sessions[0],
                "knowledge_cutoff": sessions[0],
                "source": "test",
                "source_url_or_notice_id": "test",
                "source_revision": "rules-v1",
                "evidence_path": "test",
                "notes": "test",
                "built_at": "2025-01-03T00:00:00+00:00",
            }
            rules_path = rules_dir / "ref_price_limit_regime_pit.parquet"
            pq.write_table(pa.Table.from_pylist([rule]), rules_path)
            digest = hashlib.sha256(rules_path.read_bytes()).hexdigest()
            (rules_dir / "META.json").write_text(
                json.dumps({"revision": "rules-v1", "sha256": digest}), encoding="utf-8"
            )
            policy = root / "policy.json"
            policy.write_text(POLICY_V2.read_text(encoding="utf-8"), encoding="utf-8")

            result = build_price_limit_revision(
                rules=rules_path,
                identity_revision=identity,
                s2_revision=s2,
                output=root / "output",
                revision="s3-v1",
                policy_path=policy,
            )
            rows = pq.read_table(result.output / "price_limit_execution_daily.parquet").to_pylist()

        self.assertEqual([row["execution_date"] for row in rows], sessions)
        self.assertEqual([row["eligible_session_index"] for row in rows], [1, 1, 2])
        self.assertEqual(rows[0]["reason_code"], "st_status_unknown_fail_closed")
        self.assertEqual(rows[1]["selected_rule_basis"], "st_overlay")
        self.assertEqual(rows[1]["upper_limit_pct"], 20.0)
        self.assertEqual(rows[1]["reason_code"], "session_not_tradable")
        self.assertEqual(rows[2]["eligible_session_index"], 2)
        self.assertTrue(rows[2]["is_executable"])

    def test_bulk_resolver_applies_main_board_st_rule_by_effective_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / "identity"
            identity.mkdir()
            s2 = root / "s2"
            s2.mkdir()
            rules_dir = root / "rules"
            rules_dir.mkdir()
            sessions = [date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 6)]
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-600001",
                            "stock_code": "600001",
                            "exchange": "SSE",
                            "market_date": day,
                        }
                        for day in sessions
                    ]
                ),
                identity / "daily_universe.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-600001",
                            "stock_code": "600001",
                            "exchange": "SSE",
                            "date": day,
                            "tradestatus": "1",
                            "raw_shard": "raw/600001.json",
                        }
                        for day in sessions
                    ]
                ),
                identity / "daily_isST.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-600001",
                            "stock_code": "600001",
                            "date": day,
                            "close": 10.0,
                        }
                        for day in sessions
                    ]
                ),
                identity / "daily_ohlcv_none.parquet",
            )
            (identity / "META.json").write_text("{}\n", encoding="utf-8")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-600001",
                            "execution_date": day,
                            "status_label": "st_class",
                            "is_st_class": True,
                            "status_known": True,
                            "source_market_date": day,
                            "revision": "s2-v1",
                        }
                        for day in sessions
                    ]
                ),
                s2 / "st_status_execution_daily.parquet",
            )
            (s2 / "manifest.json").write_text("{}\n", encoding="utf-8")

            unrelated_rule = {
                "interval_id": "unrelated-ipo",
                "stock_code": "300001",
                "exchange": "SZSE",
                "board": "ChiNext",
                "effective_start": date(2020, 1, 1),
                "effective_end": None,
                "upper_limit_pct": None,
                "lower_limit_pct": None,
                "listing_window_rule": "ipo_first_5_trading_days_no_limit",
                "no_limit_days": 5.0,
                "price_tick": 0.01,
                "rounding_rule": "round_to_tick_0.01_half_up_exchange_style",
                "rule_basis": "IPO_window",
                "priority": 40,
                "announcement_date": date(2020, 1, 1),
                "knowledge_cutoff": date(2020, 1, 1),
                "source": "test",
                "source_url_or_notice_id": "test",
                "source_revision": "rules-v1",
                "evidence_path": "test",
                "notes": "test",
                "built_at": "2020-01-01T00:00:00+00:00",
            }
            rules_path = rules_dir / "ref_price_limit_regime_pit.parquet"
            pq.write_table(pa.Table.from_pylist([unrelated_rule]), rules_path)
            digest = hashlib.sha256(rules_path.read_bytes()).hexdigest()
            (rules_dir / "META.json").write_text(
                json.dumps({"revision": "rules-v1", "sha256": digest}), encoding="utf-8"
            )

            result = build_price_limit_revision(
                rules=rules_path,
                identity_revision=identity,
                s2_revision=s2,
                output=root / "output",
                revision="s3-effective-date-v2",
                policy_path=POLICY_V2,
            )
            rows = pq.read_table(
                result.output / "price_limit_execution_daily.parquet"
            ).to_pylist()

        by_date = {str(row["execution_date"]): row for row in rows}
        self.assertEqual(by_date["2026-07-03"]["upper_limit_pct"], 5.0)
        self.assertEqual(by_date["2026-07-03"]["upper_limit_price"], 10.5)
        self.assertEqual(by_date["2026-07-03"]["lower_limit_price"], 9.5)
        self.assertEqual(by_date["2026-07-06"]["upper_limit_pct"], 10.0)
        self.assertEqual(by_date["2026-07-06"]["lower_limit_pct"], 10.0)
        self.assertEqual(by_date["2026-07-06"]["upper_limit_price"], 11.0)
        self.assertEqual(by_date["2026-07-06"]["lower_limit_price"], 9.0)


if __name__ == "__main__":
    unittest.main()
