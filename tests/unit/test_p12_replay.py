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


class TestFillDay:
    """Review R2 item 1: a T-evening plan fills at the T+1 OPEN, never T's."""

    def _engine(self, tmp_path, sessions, bars_by_day):
        from quanthus.p12_replay.engine import ReplayEngine
        from quanthus.p12_replay.replay_product import ReplayInputSpec
        from quanthus.p12_replay.replay_quotes import DayBar
        from quanthus.p12_daily.rule_runner import SyntheticRuleRunner
        from pathlib import Path

        class _StubApp:
            def __init__(self):
                self.observations = []
                self.reports = []

            def workbench(self, scope):
                class V:
                    pass
                return V()

            def paper_submit_order_observation(self, obs, **kw):
                self.observations.append((obs, kw))
                return obs

            def paper_submit_report(self, report):
                self.reports.append(report)

                class R:
                    status = "POSTED"
                return R()

            def rebuild_matches(self, scope):
                return True

            def paper_projection(self):
                class P:
                    cash = 0
                    positions = ()
                return P()

            def paper_history(self):
                class H:
                    orders = ()
                    reports = ()
                return H()

            @property
            def scope(self):
                return "synthetic"

        app = _StubApp()
        spec = ReplayInputSpec(
            history={"event_window_sessions": {"observed_sessions": 0}},
            corporate_actions={},
            volume={},
            knowledge_cutoff=None,
            completed_at=None,
        )
        eng = ReplayEngine(
            state_dir=tmp_path / "st",
            sessions=sessions,
            bars_by_day=bars_by_day,
            runner=SyntheticRuleRunner(candidates=()),
            spec=spec,
            base_manifest_path=tmp_path / "m.json",
            base_manifest_sha256="sha256:" + "0" * 64,
            app_builder=lambda **kw: app,
        )
        return eng, app

    def _patch_plans(self, eng, plan):
        eng._open_plans = lambda: [plan]

    def test_confirm_uses_next_session_open(self, tmp_path):
        from datetime import date
        from decimal import Decimal
        from quanthus.p12_replay.replay_quotes import DayBar

        sessions = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
        bars_by_day = {
            date(2026, 10, 19): {
                "600000": DayBar(security_code="600000", trade_date=date(2026, 10, 19),
                                 open=Decimal("9.72"), high=Decimal("9.9"),
                                 low=Decimal("9.6"), close=Decimal("9.8"),
                                 volume=1, amount=Decimal("1"))
            },
            date(2026, 10, 20): {
                "600000": DayBar(security_code="600000", trade_date=date(2026, 10, 20),
                                 open=Decimal("9.79"), high=Decimal("9.9"),
                                 low=Decimal("9.7"), close=Decimal("9.85"),
                                 volume=1, amount=Decimal("1"))
            },
        }
        eng, app = self._engine(tmp_path, sessions, bars_by_day)
        self._patch_plans(eng, {
            "plan_id": "p1", "security_code": "600000", "action_code": "BUY",
            "side": "buy", "target_qty": 1200, "delta_qty": 1200, "price_cap": "10.0",
        })
        out = eng.confirm("p1")
        assert out["status"] == "filled"
        # filled at the T+1 open (9.79), not the publish day's open (9.72)
        assert out["fill_price"] == "9.79"
        assert out["session_open"] == "9.79"
        assert out["fill_day"] == "2026-10-20"
        assert out["publish_day"] == "2026-10-19"
        # the fill posts at the T+1 09:30 open
        assert app.reports[0].occurred_at.isoformat() == "2026-10-20T09:30:00+08:00"

    def test_confirm_is_idempotent(self, tmp_path):
        from datetime import date
        from decimal import Decimal
        from quanthus.p12_replay.replay_quotes import DayBar

        sessions = [date(2026, 10, 19), date(2026, 10, 20)]
        bars_by_day = {
            d: {"600000": DayBar(security_code="600000", trade_date=d,
                                 open=Decimal("9.7"), high=Decimal("9.9"),
                                 low=Decimal("9.6"), close=Decimal("9.8"),
                                 volume=1, amount=Decimal("1"))}
            for d in sessions
        }
        eng, app = self._engine(tmp_path, sessions, bars_by_day)
        plan = {"plan_id": "p1", "security_code": "600000", "action_code": "BUY",
                "side": "buy", "target_qty": 100, "delta_qty": 100, "price_cap": "10.0"}
        self._patch_plans(eng, plan)
        first = eng.confirm("p1")
        # plan no longer open -> without the record a repeat would PLAN_NOT_FOUND
        self._patch_plans(eng, plan)  # still listed; the record must short-circuit
        second = eng.confirm("p1")
        assert second.get("idempotent_replay") is True
        assert second["fill_price"] == first["fill_price"]
        assert len(app.reports) == 1  # one fill only, never double-posted

    def test_observed_sessions_override_reaches_spec(self, tmp_path):
        """Regression: make_engine must wire observed_sessions_by_day into the
        per-T spec - otherwise the floors report the replay window's few
        sessions (INPUT_BLOCKED everywhere) instead of the authoritative
        Base count through T."""
        from datetime import date
        from decimal import Decimal
        from unittest.mock import patch
        from quanthus.p12_replay.driver import make_engine
        from quanthus.p12_replay.replay_quotes import DayBar

        sessions = [date(2026, 10, 28), date(2026, 10, 29)]
        bars_by_day = {
            d: {"600000": DayBar(security_code="600000", trade_date=d,
                                 open=Decimal("9.7"), high=Decimal("9.9"),
                                 low=Decimal("9.6"), close=Decimal("9.8"),
                                 volume=1, amount=Decimal("1"))}
            for d in sessions
        }
        observed = {"2026-10-28": 188, "2026-10-29": 189}
        eng = make_engine(
            state_dir=tmp_path / "st",
            sessions=sessions,
            bars_by_day=bars_by_day,
            candidates_path=None,
            fixture_path=None,
            observed_sessions_by_day=observed,
            reset=True,
        )
        # The engine applies the per-T override itself; verify by calling
        # materialize and inspecting the spec it hands write_replay_day.
        seen = {}

        def _spy(*args, **kwargs):
            seen["spec"] = kwargs["spec"]
            raise SystemExit("stop")  # we only need the spec

        with patch(
            "quanthus.p12_replay.engine.write_replay_day", side_effect=_spy
        ):
            try:
                eng.materialize_day(date(2026, 10, 28))
            except SystemExit:
                pass
        spec_used = seen["spec"]
        assert spec_used.history["listing_sessions"]["observed_sessions"] == 188
        assert spec_used.history["event_window_sessions"]["observed_sessions"] == 188
