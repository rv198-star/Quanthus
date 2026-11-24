'''Issue #748 unit tests: replay calendar, bar slicing, fill rule, engine.'''

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.p12_replay.replay_calendar import write_replay_calendar
from quanthus.p12_replay.replay_quotes import (
    DailyBarQuoteService,
    DayBar,
    match_limit,
)
from quanthus.p12_replay.replay_product import ReplayInputSpec, write_replay_day

TZ = timezone(timedelta(hours=8))


class TestCalendar:
    def test_round_trip_loadable(self, tmp_path):
        sessions = [date(2026, 10, 19) + timedelta(days=i) for i in range(5)]
        sessions = [d for d in sessions if d.weekday() < 5]
        root = tmp_path / "cal"
        write_replay_calendar(
            root, sessions=sessions, version="v1",
            available_at=datetime(2026, 10, 19, 8, 0, tzinfo=TZ),
        )
        from quanthus.p12_daily.forward_calendar import load_forward_calendar, next_session

        cal, block, receipt = load_forward_calendar(
            root, decision_cutoff=datetime(2026, 10, 19, 22, 30, tzinfo=TZ)
        )
        assert cal is not None, block
        nxt, err = next_session(cal, sessions[0])
        assert err is None
        assert nxt == sessions[1]

    def test_out_of_coverage_blocks(self, tmp_path):
        sessions = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
        root = tmp_path / "cal"
        write_replay_calendar(
            root, sessions=sessions, version="v1",
            available_at=datetime(2026, 10, 19, 8, 0, tzinfo=TZ),
        )
        from quanthus.p12_daily.forward_calendar import load_forward_calendar, next_session

        cal, block, _ = load_forward_calendar(
            root, decision_cutoff=datetime(2026, 10, 19, 22, 30, tzinfo=TZ)
        )
        nxt, err = next_session(cal, date(2026, 10, 22))
        assert nxt is None and err is not None


class TestFillRule:
    def _bar(self, low="10.00", high="10.80", open_="10.40", close="10.60"):
        return DayBar(
            security_code="600000",
            trade_date=date(2026, 10, 20),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
        )

    def test_buy_fills_at_open(self):
        # P11 口径: fills at the real open, never intraday high/low.
        assert match_limit("buy", Decimal("10.75"), self._bar()) == Decimal("10.40")

    def test_buy_no_fill_when_open_above_cap(self):
        assert match_limit("buy", Decimal("10.35"), self._bar()) is None

    def test_sell_fills_at_open(self):
        assert match_limit("sell", Decimal("10.30"), self._bar()) == Decimal("10.40")

    def test_sell_no_fill_when_open_below_floor(self):
        assert match_limit("sell", Decimal("10.50"), self._bar()) is None


class TestQuotes:
    def test_unavailable_when_no_bar(self):
        svc = DailyBarQuoteService(
            session_date=date(2026, 10, 20), bars={},
        )
        q = svc.quote("600000")
        assert q.status == "unavailable" and q.last_price is None

    def test_live_quote_carries_session_close(self):
        bar = DayBar(
            security_code="600000",
            trade_date=date(2026, 10, 20),
            open=Decimal("10.40"),
            high=Decimal("10.80"),
            low=Decimal("10.30"),
            close=Decimal("10.60"),
        )
        prev = DayBar(
            security_code="600000",
            trade_date=date(2026, 10, 19),
            open=Decimal("10.10"),
            high=Decimal("10.50"),
            low=Decimal("10.00"),
            close=Decimal("10.45"),
        )
        svc = DailyBarQuoteService(
            session_date=date(2026, 10, 20),
            bars={"600000": bar},
            previous={"600000": prev},
        )
        q = svc.quote("600000")
        assert q.status == "live"
        assert q.last_price == Decimal("10.60")
        assert q.previous_close == Decimal("10.45")
        assert q.quote_time == datetime(2026, 10, 20, 15, 0, tzinfo=TZ)


class TestProduct:
    def test_manifest_verifies(self, tmp_path):
        pytest.importorskip("pyarrow")
        from quanthus.p12_replay.bar_slice import write_session_parquet

        base = tmp_path / "base.json"
        base.write_text('{"kind":"base"}')
        base_sha = "sha256:" + __import__("hashlib").sha256(base.read_bytes()).hexdigest()
        parquet = tmp_path / "bars.parquet"
        write_session_parquet(
            {
                "600000": DayBar(
                    security_code="600000",
                    trade_date=date(2026, 10, 20),
                    open=Decimal("10.40"),
                    high=Decimal("10.80"),
                    low=Decimal("10.30"),
                    close=Decimal("10.60"),
                )
            },
            parquet,
        )
        spec = ReplayInputSpec(
            history={
                "event_window_sessions": {"observed_sessions": 30},
                "feature_warmup_sessions": {"observed_sessions": 80},
                "listing_sessions": {"observed_sessions": 200},
            },
            corporate_actions={
                "fields_complete": True,
                "cash_dividend_per_share": "0.00",
                "bonus_share_ratio": "0.00",
                "exceptions": [],
            },
            volume={"volume_scope": "UNKNOWN"},
            knowledge_cutoff="2026-10-20T18:00:00+08:00",
            completed_at="2026-10-20T18:01:00+08:00",
        )
        product = write_replay_day(
            tmp_path / "job",
            market_date=date(2026, 10, 20),
            bars_parquet=parquet,
            row_counts={"dwd_security_bar": 1},
            spec=spec,
            base_manifest_path=base,
            base_manifest_sha256=base_sha,
        )
        from quanthus.p12_daily.daily_core_input import qualify_daily_input

        result = qualify_daily_input(
            product.pointer_path,
            expected_market_date=date(2026, 10, 20),
            checked_at=datetime(2026, 10, 20, 22, 35, tzinfo=TZ),
            base_manifest_pin=None,
        )
        assert not hasattr(result, "reason_code") or result.reason_code != "INPUT_UNAVAILABLE", result
        assert result.market_date == date(2026, 10, 20)
        assert result.listed_securities == ("600000",)


class TestCnPresentation:
    def test_action_labels_and_reason_mapping(self):
        from quanthus.p12_replay.replay_cn import (
            action_label,
            plan_reason,
            reason_text,
        )

        assert reason_text("EXIT_SIGNAL") == "达到退出条件，按计划卖出"
        assert reason_text("NOT_A_CODE") == "详见记录页"
        assert action_label({"action_code": "BUY"}) == "买入建议"
        assert (
            action_label({"action_code": "SELL", "exit_kind": "reduce"})
            == "减仓建议"
        )
        item = {
            "reason_codes": ("NEW_ENTRY_SIGNAL",),
            "explanations": [
                {"text": "[synthetic] batch-A replay rule emitted this entry"},
                {"text": "候选股的流动性满足合成规则的检查"},
            ],
        }
        lines = plan_reason(item)
        assert lines == ["候选股的流动性满足合成规则的检查"]

    def test_invalidation_translation(self):
        from quanthus.p12_replay.replay_cn import invalidation_texts

        lines = invalidation_texts(
            {
                "invalidations": (
                    "开盘价高于价格上限时不追买",
                    "requires:PREOPEN_LIMIT_PRICE",
                )
            }
        )
        assert lines[0] == "开盘价高于价格上限时不追买"
        assert "requires:PREOPEN" not in lines[1]
        assert "开盘前事实" in lines[1]
