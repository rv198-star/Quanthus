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
        # Confirming before the bound session's 09:30 open registers the
        # intent only; the fill posts once the clock reaches it (R3 #1).
        eng.set_now(datetime(2026, 10, 19, 23, 0, tzinfo=TZ))
        early = eng.confirm("p1")
        assert early["status"] == "intent_registered"
        assert early["fill_day"] == "2026-10-20"
        # Reaching the execution instant posts the real T+1 open fill.
        eng.set_now(datetime(2026, 10, 20, 9, 30, tzinfo=TZ))
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
        # Advance the clock to the execution instant so the first confirm fills.
        eng.set_now(datetime(2026, 10, 20, 9, 30, tzinfo=TZ))
        first = eng.confirm("p1")
        assert first["status"] == "filled"
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


    def _bar(self, code, d, open_="9.79"):
        from decimal import Decimal
        from quanthus.p12_replay.replay_quotes import DayBar
        return DayBar(security_code=code, trade_date=d, open=Decimal(open_),
                      high=Decimal("9.9"), low=Decimal("9.6"),
                      close=Decimal("9.8"), volume=1, amount=Decimal("1"))

    def test_auto_advance_settles_registered_intent_at_bound_open(self, tmp_path):
        """R4 #1: an intent registered before the bound session's open is
        filled by the SAME advance that opens that session (auto == manual)."""
        from datetime import date
        from decimal import Decimal
        sessions = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
        bars_by_day = {d: {"600000": self._bar("600000", d)} for d in sessions}
        eng, app = self._engine(tmp_path, sessions, bars_by_day)
        self._patch_plans(eng, {
            "plan_id": "p1", "security_code": "600000", "action_code": "BUY",
            "side": "buy", "target_qty": 1200, "delta_qty": 1200, "price_cap": "10.0",
        })
        # 10-19 晚登记意图
        eng.set_now(datetime(2026, 10, 19, 23, 0, tzinfo=TZ))
        early = eng.confirm("p1")
        assert early["status"] == "intent_registered"
        bound = early["fill_day"]  # package-bound (next session here)
        # 手动推进越过绑定开盘：advance 里 09:30 撮合已登记意图 -> 同一账本
        eng.advance_day()
        rec = eng._confirm_records["p1"]
        assert rec["status"] == "filled"
        assert rec["fill_day"] == bound
        assert rec["session_open"] == "9.79"
        assert len(app.reports) == 1

    def test_late_confirm_after_bound_open_expires(self, tmp_path):
        """R4 #1: a confirm arriving AFTER the bound open, with no registered
        intent, is refused EXPIRED - never back-filled to the past open."""
        from datetime import date
        sessions = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
        bars_by_day = {d: {"600000": self._bar("600000", d)} for d in sessions}
        eng, app = self._engine(tmp_path, sessions, bars_by_day)
        self._patch_plans(eng, {
            "plan_id": "p1", "security_code": "600000", "action_code": "BUY",
            "side": "buy", "target_qty": 100, "delta_qty": 100, "price_cap": "10.0",
        })
        # 真实过期：放出意图、推进越过绑定开盘但未确认就推进到再下一天
        # -> 意图已过期。先把 10-19 晚意图挂起，推进到 10-20（成交窗口到
        # 达但未确认，10-20 该意图本应在开盘成交：先验同一账本），再单独
        # 验证「从未登记的确认在绑定开盘后来到时过期拒绝」。
        eng3, app3 = self._engine(tmp_path / "c", sessions, bars_by_day)
        self._patch_plans(eng3, {
            "plan_id": "p3", "security_code": "600000", "action_code": "BUY",
            "side": "buy", "target_qty": 100, "delta_qty": 100, "price_cap": "10.0",
        })
        eng3.set_now(datetime(2026, 10, 19, 23, 0, tzinfo=TZ))
        e3 = eng3.confirm("p3")
        assert e3["status"] == "intent_registered"
        bound3 = e3["fill_day"]
        # 推进到绑定日 -> 已登记意图在开盘成交（自动/手动同账）
        eng3.advance_day()
        assert eng3._confirm_records["p3"]["status"] == "filled"
        assert len(app3.reports) == 1
        # 另一条：从未登记的计划在绑定开盘后来确认 -> 过期拒绝
        eng4, app4 = self._engine(tmp_path / "d", sessions, bars_by_day)
        self._patch_plans(eng4, {
            "plan_id": "p4", "security_code": "600000", "action_code": "BUY",
            "side": "buy", "target_qty": 100, "delta_qty": 100, "price_cap": "10.0",
        })
        # 计划在 10-19 的 publish_day 先落档（模拟当日已放出但不确认）
        eng4.set_now(datetime(2026, 10, 19, 23, 0, tzinfo=TZ))
        eng4._plan_publish_day["p4"] = "2026-10-19"
        eng4._save_records("publish-day")
        # 不调用 confirm，直接推进越过绑定开盘
        eng4.advance_day()  # opens 10-20
        eng4.advance_day()  # opens 10-21
        eng4.set_now(datetime(2026, 10, 21, 22, 0, tzinfo=TZ))
        out4 = eng4.confirm("p4")
        assert out4["status"] == "expired"
        assert out4["reason"] == "BOUND_SESSION_OPEN_PASSED_UNCONFIRMED"
        assert out4["bound_fill_day"] == "2026-10-20"
        assert len(app4.reports) == 0
        # 用一条更真实的：放出 10-19 意图、推进到 10-20 让它成交，再试图
        # 对同一计划重复确认 -> 幂等回执，不会二次成交
        eng2, app2 = self._engine(tmp_path / "b", sessions, bars_by_day)
        self._patch_plans(eng2, {
            "plan_id": "p2", "security_code": "600000", "action_code": "BUY",
            "side": "buy", "target_qty": 100, "delta_qty": 100, "price_cap": "10.0",
        })
        eng2.set_now(datetime(2026, 10, 19, 23, 0, tzinfo=TZ))
        e2 = eng2.confirm("p2")
        assert e2["status"] == "intent_registered"
        eng2.advance_day()
        assert eng2._confirm_records["p2"]["status"] == "filled"
        again = eng2.confirm("p2")
        assert again.get("idempotent_replay") is True
        assert len(app2.reports) == 1

    def test_day_review_surfaces_skip_receipt(self, tmp_path):
        """R4 #2: a skipped plan's review shows the real skip receipt + time,
        never a bare 未处理."""
        from datetime import date
        sessions = [date(2026, 10, 19), date(2026, 10, 20)]
        bars_by_day = {d: {"600000": self._bar("600000", d)} for d in sessions}
        eng, app = self._engine(tmp_path, sessions, bars_by_day)

        class _Rec(dict):
            pass
        # records_view returns skips; day_review binds them by plan_id
        recs = {
            "reports": [], "orders": [], "events": [],
            "plans": [{"plan_id": "p1", "status": "SKIPPED"}],
            "skips": [{"plan_id": "p1", "skipped_at": "2026-10-19T23:05:00+08:00",
                       "reason": "manual", "actor": "desktop:SYN-PAPER-A"}],
        }
        eng.records_view = lambda: recs
        eng.day_state = lambda: {
            "workbench": {"opportunities": [{
                "plan": {"plan_id": "p1"}, "security_code": "600000",
                "action_code": "BUY", "status_code": "SKIPPED",
            }]},
            "account": {},
        }
        review = eng.day_review()
        adv = review["advice"][0]
        assert adv["owner_action"] == "已跳过"
        assert adv["skip_receipt"]["skipped_at"] == "2026-10-19T23:05:00+08:00"
        assert adv["skip_receipt"]["plan_id"] == "p1"

    def test_driver_blocks_on_exclusion_sha_mismatch(self, tmp_path, capsys):
        """R4 #3: the production entry refuses to enter the engine when the
        #737 file's bytes do not match the pinned digest, and writes a
        blocking receipt."""
        from datetime import date
        from quanthus.p12_replay import driver
        bad = tmp_path / "excl.json"
        bad.write_text('{"securities": ["600000"], "excluded_dates": ["2026-10-20"]}')
        state = tmp_path / "st"
        rc = driver.main([
            "--state-dir", str(state), "--start", "2026-10-19",
            "--end", "2026-10-21",
        ]) if False else None  # exercise the pre-read block directly
        # Call the block via the patched env path
        import os, json
        os.environ["P12_REPLAY_EXCLUSION"] = str(bad)
        try:
            rc = driver.main(["--state-dir", str(state),
                              "--start", "2026-10-19", "--end", "2026-10-21"])
        finally:
            os.environ.pop("P12_REPLAY_EXCLUSION", None)
        assert rc == 2
        block = json.loads((state / "replay-exclusion-block.json").read_text())
        assert block["status"] == "blocked"
        assert block["reason"] == "EXCLUSION_SHA_MISMATCH"


class TestCoverageDisplay:
    '''R5 预检实证: coverage reads the same-day p11.paper-replay-day
    receipt (or a run dict's source_closure/evaluation_receipt), reports
    the real N/M, keeps natural-language scan reasons verbatim, lists
    zero-signal evaluated securities, and keeps out-of-pool held rows
    separate from the pool counts.'''

    def _receipt(self):
        return {
            "schema_version": "p11.paper-replay-day.v1",
            "delivered": {
                "600000": {"bar_rows": 61, "window_sessions": 61,
                           "gap_sessions": 0, "entry_eligible": True},
                "000002": {"bar_rows": 61, "window_sessions": 61,
                           "gap_sessions": 0, "entry_eligible": True},
                "601390": {"bar_rows": 40, "window_sessions": 61,
                           "gap_sessions": 21, "entry_eligible": False},
            },
            "seam": {
                "evaluated_universe": ["600000", "000002", "300750"],
                "decisions": [
                    {"security_code": "000002", "disposition": "REVIEW",
                     "reason": "SOME_CODE"},
                ],
                "excluded": [],
            },
            "source_closure": {
                "held_decisions": [
                    {"security_code": "300750", "action": "held",
                     "reason": "HELD_OK"},
                ],
                "history_receipts": [
                    {"security_code": "600000", "status": "not_qualified",
                     "gates": {"events": {"reason": "no qualified event in the 22-session window"}}},
                ],
            },
            "excluded_security_codes": ["300999"],
            "coverage_exclusions": [
                {"security_code": "601390", "reason": "ACTION_COVERAGE_UNKNOWN"},
            ],
        }

    def test_real_receipt_counts_and_rows(self):
        from quanthus.p12_replay.coverage_display import coverage_block

        block = coverage_block(self._receipt())
        # Pool M = delivered entries (3); held 300750 is outside the pool.
        assert block["pool_count"] == 3
        # N = evaluated members of the pool only (600000 + 000002); the
        # out-of-pool held 300750 never inflates N or M.
        assert block["evaluated_count"] == 2
        assert block["pending_count"] == 1
        assert block["note_text"].startswith("本日可评估 2 只 / 策略池 3 只")
        rows = {r["security_code"]: r for r in block["securities"]}
        # 600000: scanned, no qualified event -> evaluated, entry-fail
        # verdict and the event-scan reason, never a blank row.
        assert rows["600000"]["evaluated"] is True
        assert rows["600000"]["verdict"] == "未通过入场"
        assert "22-session" in rows["600000"]["scan_reason"]
        # 000002: real decision verdict + reason mapping.
        assert rows["000002"]["verdict"] == "待复核"
        # Out-of-pool held row kept, marked not-in-pool, not counted.
        assert rows["300750"]["in_pool"] is False
        assert rows["300750"]["evaluated"] is False
        assert rows["300750"]["state"] == "held"
        # Coverage-isolated member pending with a clearance reason.
        assert rows["601390"]["evaluated"] is False
        assert rows["601390"]["state"] == "pending"
        assert rows["601390"]["scan_reason"]

    def test_natural_language_reason_preserved(self):
        from quanthus.p12_replay.coverage_display import coverage_block

        r = self._receipt()
        # A CJK / sentence reason on a decision passes through verbatim,
        # never collapsed to the generic 详见记录页 label.
        r["seam"]["decisions"][0]["reason"] = "趋势在 61 日窗口内走弱，触发复核"
        block = coverage_block(r)
        rows = {x["security_code"]: x for x in block["securities"]}
        assert rows["000002"]["scan_reason"] == "趋势在 61 日窗口内走弱，触发复核"

    def test_zero_signal_and_blocked_pair(self):
        from quanthus.p12_replay.coverage_display import coverage_block

        # Zero-signal: everyone evaluated, no decisions, all listed.
        r = self._receipt()
        r["seam"]["decisions"] = []
        block = coverage_block(r)
        assert block["evaluated_count"] == 2
        assert all(
            x["evaluated"] for x in block["securities"] if x["in_pool"]
            and x["entry_eligible"]
        )
        # Whole-day blocked: the receipt carries a block, coverage still
        # reports the delivered pool and the named block reason.
        r2 = self._receipt()
        r2["day_block"] = {"reason": "history_gate_unsatisfied",
                           "scope": "whole_day"}
        r2["seam"]["evaluated_universe"] = []
        block2 = coverage_block(r2)
        assert block2["pool_count"] == 3
        assert block2["evaluated_count"] == 0

    def test_run_dict_carrier_and_blocked_fallback(self):
        from quanthus.p12_replay.coverage_display import (
            coverage_block,
            pool_only_block,
        )
        # A P12 run dict carrying source_closure + evaluation_receipt is
        # a valid carrier (seam under evaluation_receipt).
        r = {
            "schema_version": "quanthus.p12.daily-run.v1",
            "status": "PUBLISHED",
            "evaluation_receipt": {
                "seam": {"evaluated_universe": ["600000"],
                          "decisions": [{"security_code": "600000",
                                          "disposition": "ENTRY",
                                          "reason": "NEW_ENTRY_SIGNAL"}]},
                "delivered": {"600000": {"entry_eligible": True}},
            },
            "source_closure": {"held_decisions": [], "history_receipts": []},
        }
        block = coverage_block(r)
        assert block["pool_count"] == 1
        assert block["evaluated_count"] == 1
        # INPUT_BLOCKED run dict: no seam/source_closure coverage -> None.
        blocked = {"schema_version": "quanthus.p12.daily-run.v1",
                   "status": "INPUT_BLOCKED",
                   "input_block": {"reason_code": "INPUT_UNAVAILABLE"}}
        assert coverage_block(blocked) is None
        only = pool_only_block(2, delivered_codes=["600000", "000002"])
        assert only["pool_count"] == 2
        assert only["evaluated_count"] is None


class TestR5ExpireAndSkip:
    '''R5 #1: advancing into the bound session's open proactively expires a
    never-confirmed executable advice - no confirm click needed.  A new
    package, a later day or a restart reuses the SAME original-ID receipt.
    R5 #2: a SKIPPED plan is listed by plan_id with its real receipt.'''

    def _bar(self, code, d, open_="9.79"):
        from decimal import Decimal
        from quanthus.p12_replay.replay_quotes import DayBar
        return DayBar(security_code=code, trade_date=d, open=Decimal(open_),
                      high=Decimal("9.9"), low=Decimal("9.6"),
                      close=Decimal("9.8"), volume=1, amount=Decimal("1"))

    def _engine(self, tmp_path, sessions, bars_by_day):
        from quanthus.p12_replay.engine import ReplayEngine
        from quanthus.p12_replay.replay_product import ReplayInputSpec
        from quanthus.p12_daily.rule_runner import SyntheticRuleRunner
        class _App:
            def __init__(self):
                self.reports = []
                self.observations = []
            def workbench(self, scope):
                class V: pass
                return V()
            def records(self, scope):
                class V:
                    history = type('H',(),{'outbox_pending':0})()
                    reports=(); orders=(); plans=(); events=()
                return V()
            def paper_projection(self):
                class P: cash=0; positions=()
                return P()
            def paper_history(self):
                class H: orders=(); reports=()
                return H()
            def rebuild_matches(self, scope): return True
            @property
            def scope(self): return "synthetic"
        spec = ReplayInputSpec(history={}, corporate_actions={}, volume={},
                               knowledge_cutoff=None, completed_at=None)
        app = _App()
        eng = ReplayEngine(state_dir=tmp_path/"st", sessions=sessions,
                           bars_by_day=bars_by_day,
                           runner=SyntheticRuleRunner(candidates=()), spec=spec,
                           base_manifest_path=tmp_path/"m.json",
                           base_manifest_sha256="sha256:"+"0"*64,
                           app_builder=lambda **kw: app)
        return eng, app

    def test_unconfirmed_executable_expires_at_bound_open(self, tmp_path):
        from datetime import date
        sessions = [date(2026,10,19), date(2026,10,20), date(2026,10,21)]
        bars = {d:{"600000": self._bar("600000", d)} for d in sessions}
        eng, app = self._engine(tmp_path, sessions, bars)
        plan = {"plan_id":"p1","security_code":"600000","action_code":"BUY",
                "side":"buy","target_qty":100,"delta_qty":100,"price_cap":"10.0"}
        eng._open_plans = lambda: [plan]
        # Offered 10-19 evening, never confirmed; bind its exec to 10-20 open.
        eng.set_now(datetime(2026,10,19,23,0,tzinfo=TZ))
        eng._plan_publish_day["p1"] = "2026-10-19"
        eng._save_records("publish-day")
        eng._bound_package = lambda pid: (date(2026,10,19), date(2026,10,20),
                                          datetime(2026,10,20,9,30,tzinfo=TZ), "")
        # Advance into 10-20: the bound open passes with no confirm ->
        # proactive expired terminal + receipt, zero orders/fills.
        eng.advance_day()
        rec = eng._confirm_records["p1"]
        assert rec["status"] == "expired"
        assert rec["reason"] == "BOUND_SESSION_OPEN_PASSED_UNCONFIRMED"
        assert rec["bound_fill_day"] == "2026-10-20"
        assert len(app.reports) == 0 and len(app.observations) == 0
        # Late first confirm reuses the SAME receipt (idempotent).
        out = eng.confirm("p1")
        assert out["status"] == "expired"
        assert out.get("idempotent_replay") is True
        assert len(app.reports) == 0

    def test_skip_receipt_listed_in_plans(self, tmp_path):
        from datetime import date
        sessions = [date(2026,10,19), date(2026,10,20)]
        bars = {d:{"600000": self._bar("600000", d)} for d in sessions}
        eng, app = self._engine(tmp_path, sessions, bars)
        # The store reports a persisted skip receipt; the app's history
        # plans list lacks it - records_view must append the SKIPPED row.
        eng._store = type('S',(),{'skipped_plans': staticmethod(
            lambda: {"p1":{"plan_id":"p1","skipped_at":"2026-10-19T23:05:00+08:00",
                           "reason":"manual","actor":"desktop"}})})()
        class V:
            history = type('H',(),{'outbox_pending':0})()
            reports=(); orders=(); events=()
            plans=()  # app does NOT write a SKIPPED status row
        eng._build_app = lambda: type('A',(),{'records':lambda s,scope: V(),
            'workbench':lambda s,scope: V(), 'scope':'synthetic',
            'rebuild_matches':lambda s,scope:True,
            'paper_projection':lambda s: type('P',(),{'cash':0,'positions':()})(),
            'paper_history':lambda s: type('H',(),{'orders':(),'reports':()})()})()
        recs = eng.records_view()
        assert {"plan_id":"p1","status":"SKIPPED"} in recs["plans"]
        assert recs["skips"][0]["plan_id"] == "p1"


class TestCoveragePrecheckRound2:
    '''R5 覆盖第二轮预检: real-shape exclusions, sidecar bound to the
    current run, and the missing authoritative denominator.'''

    def _receipt(self):
        return {
            "schema_version": "p11.paper-replay-day.v1",
            "market_date": "2026-10-08",
            "scenario_id": "scn-1",
            "delivered": {
                "600000": {"entry_eligible": True},
                "000002": {"entry_eligible": True},
            },
            "seam": {"evaluated_universe": ["600000", "000002"],
                      "decisions": [], "excluded": []},
            "source_closure": {
                "held_decisions": [],
                "history_receipts": [],
                # Real shape: exclusions are OBJECTS, not bare strings.
                "exclusions": [
                    {"security_code": "300999", "reason": "ex"},
                    {"security_code": "300998", "reason": "ex2"},
                ],
            },
            "excluded_security_codes": [],
            "coverage_exclusions": [],
        }

    def test_object_exclusions_projected_no_typeerror(self):
        from quanthus.p12_replay.coverage_display import coverage_block
        block = coverage_block(self._receipt())
        # Empty #737 + per-security object exclusions -> no TypeError,
        # both counted into the day's exclusion note.
        assert block["pool_count"] == 2
        assert "当日排除 2 只" in block["note_text"]

    def test_missing_denominator_reports_state_not_fake_pool(self):
        from quanthus.p12_replay.coverage_display import coverage_block
        r = self._receipt()
        del r["delivered"]
        # A single history_receipt must NOT read as "策略池 1 只".
        r["source_closure"]["history_receipts"] = [
            {"security_code": "600000",
             "gates": {"events": {"reason": "no qualified event"}}},
        ]
        block = coverage_block(r)
        assert block["pool_count"] is None
        assert block["denominator_known"] is False
        assert "分母" in block["note_text"]
        # per-security evidence still readable
        assert block["securities"]

    def test_denominator_missing_with_day_block(self):
        from quanthus.p12_replay.coverage_display import coverage_block
        r = self._receipt()
        del r["delivered"]
        r["day_block"] = {"reason": "history_gate_unsatisfied",
                          "scope": "whole_day"}
        r["seam"]["evaluated_universe"] = []
        block = coverage_block(r)
        assert block["pool_count"] is None
        assert "已阻断" in block["note_text"]

    def test_sidecar_bound_to_current_run(self, tmp_path):
        from datetime import date
        import json, hashlib
        from quanthus.p12_replay.engine import ReplayEngine
        from quanthus.p12_replay.replay_product import ReplayInputSpec
        from quanthus.p12_daily.rule_runner import SyntheticRuleRunner

        sessions = [date(2026,10,8), date(2026,10,9)]
        spec = ReplayInputSpec(history={}, corporate_actions={}, volume={},
                               knowledge_cutoff=None, completed_at=None)
        state = tmp_path / "st"
        eng = ReplayEngine(
            state_dir=state, sessions=sessions, bars_by_day={},
            runner=SyntheticRuleRunner(candidates=()), spec=spec,
            base_manifest_path=tmp_path/"m.json",
            base_manifest_sha256="sha256:"+"0"*64,
            app_builder=lambda **kw: None)
        # A real sidecar for 10-08 bound to THIS run; another-date file
        # planted at a date-matching path must NOT be picked up on its own.
        day = "2026-10-08"
        real = self._receipt()
        real_path = state / "receipts" / "run-1.json"
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(real))
        real_sha = "sha256:" + hashlib.sha256(real_path.read_bytes()).hexdigest()
        other = self._receipt(); other["market_date"] = "2026-10-09"
        # date-named path that would have been wrongly picked up before
        (state/"replay-day-receipts").mkdir(exist_ok=True)
        (state/"replay-day-receipts"/f"{day}.json").write_text(json.dumps(other))

        class _Store:
            def latest_run_for_date(self, d):
                return {"run_id":"run-1","scenario_id":"scn-1",
                        "market_date": day,
                        "p11_replay_day_receipt_ref": {
                            "path": "receipts/run-1.json",
                            "sha256": real_sha,
                            "scenario_id": "scn-1",
                            "run_id": "run-1"}}
        eng._store = _Store()
        doc = eng._day_replay_receipt()
        assert doc is not None and doc["market_date"] == day
        # Tampered scenario in ref -> refused, not silently read.
        class _Store2(_Store):
            def latest_run_for_date(self, d):
                r = super().latest_run_for_date(d)
                r["p11_replay_day_receipt_ref"] = dict(
                    r["p11_replay_day_receipt_ref"], scenario_id="scn-OTHER")
                return r
        eng._store = _Store2()
        assert eng._day_replay_receipt() is None

    def _engine(self, tmp_path, state):
        from datetime import date
        from quanthus.p12_replay.engine import ReplayEngine
        from quanthus.p12_replay.replay_product import ReplayInputSpec
        from quanthus.p12_daily.rule_runner import SyntheticRuleRunner

        sessions = [date(2026, 10, 8), date(2026, 10, 9)]
        spec = ReplayInputSpec(history={}, corporate_actions={}, volume={},
                               knowledge_cutoff=None, completed_at=None)
        return ReplayEngine(
            state_dir=state, sessions=sessions, bars_by_day={},
            runner=SyntheticRuleRunner(candidates=()), spec=spec,
            base_manifest_path=tmp_path / "m.json",
            base_manifest_sha256="sha256:" + "0" * 64,
            app_builder=lambda **kw: None)

    def test_real_writer_ref_shape_reads_original(self, tmp_path):
        # The REAL DailyPackageStore._day_receipt_ref payload carries
        # schema_version + run_id + path + sha256 + market_date +
        # scenario_id and nothing else - the old code read schema_version
        # as "inline receipt" and returned the bare ref (no delivered).
        import json, hashlib
        state = tmp_path / "st"
        eng = self._engine(tmp_path, state)
        day = "2026-10-08"
        original = self._receipt()
        # Real layout: <state>/runs/replay-day-receipts/<run_id>.json
        real_path = state / "runs" / "replay-day-receipts" / "run-9.json"
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(original))
        real_sha = "sha256:" + hashlib.sha256(
            real_path.read_bytes()).hexdigest()

        class _Store:
            def latest_run_for_date(self, d):
                return {
                    "run_id": "run-9", "market_date": day,
                    "scenario_id": "scn-1",
                    "p11_replay_day_receipt_ref": {
                        # exact writer shape
                        "schema_version": "p11.paper-replay-day.v1",
                        "run_id": "run-9",
                        "path": "runs/replay-day-receipts/run-9.json",
                        "sha256": real_sha,
                        "market_date": day,
                        "scenario_id": "scn-1",
                    }}
        eng._store = _Store()
        doc = eng._day_replay_receipt()
        # The ORIGINAL receipt comes back - its coverage is readable.
        assert doc is not None
        assert doc["delivered"]["600000"]["entry_eligible"] is True
        assert doc["market_date"] == day

        # Bare-hex sha256 (no sha256: prefix) -> refused.
        class _StoreNoShaPfx(_Store):
            def latest_run_for_date(self, d):
                r = super().latest_run_for_date(d)
                r["p11_replay_day_receipt_ref"] = dict(
                    r["p11_replay_day_receipt_ref"],
                    sha256=real_sha.removeprefix("sha256:"))
                return r
        eng._store = _StoreNoShaPfx()
        assert eng._day_replay_receipt() is None

        # Missing sha256 entirely -> refused.
        class _StoreNoSha(_Store):
            def latest_run_for_date(self, d):
                r = super().latest_run_for_date(d)
                ref = dict(r["p11_replay_day_receipt_ref"])
                ref.pop("sha256")
                r["p11_replay_day_receipt_ref"] = ref
                return r
        eng._store = _StoreNoSha()
        assert eng._day_replay_receipt() is None

    def test_real_writer_ref_foreign_scenario_refused(self, tmp_path):
        # The bound file exists and hashes fine, but the ORIGINAL doc's
        # scenario differs from this run's - refused, not served.
        import json, hashlib
        state = tmp_path / "st"
        eng = self._engine(tmp_path, state)
        day = "2026-10-08"
        original = self._receipt()
        original["scenario_id"] = "scn-FOREIGN"
        real_path = state / "runs" / "replay-day-receipts" / "run-9.json"
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(original))
        real_sha = "sha256:" + hashlib.sha256(
            real_path.read_bytes()).hexdigest()

        class _Store:
            def latest_run_for_date(self, d):
                return {
                    "run_id": "run-9", "market_date": day,
                    "scenario_id": "scn-1",
                    "p11_replay_day_receipt_ref": {
                        "schema_version": "p11.paper-replay-day.v1",
                        "run_id": "run-9",
                        "path": "runs/replay-day-receipts/run-9.json",
                        "sha256": real_sha,
                        "market_date": day,
                        "scenario_id": "scn-FOREIGN",
                    }}
        eng._store = _Store()
        assert eng._day_replay_receipt() is None

    def test_ref_missing_run_id_refused(self, tmp_path):
        # 协调 R6 item 1: a ref that declares NO run_id can never bind
        # the receipt to this run - refused, not read.
        import json, hashlib
        state = tmp_path / "st"
        eng = self._engine(tmp_path, state)
        day = "2026-10-08"
        original = self._receipt()
        real_path = state / "runs" / "replay-day-receipts" / "run-9.json"
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(original))
        self._real_sha = "sha256:" + hashlib.sha256(
            real_path.read_bytes()).hexdigest()
        # ref carries every field EXCEPT run_id
        ref = {"schema_version": "p11.paper-replay-day.v1",
               "path": "runs/replay-day-receipts/run-9.json",
               "sha256": self._real_sha, "market_date": day,
               "scenario_id": "scn-1"}
        run = {"run_id": "run-9", "market_date": day, "scenario_id": "scn-1",
               "p11_replay_day_receipt_ref": ref}
        class _S:
            def latest_run_for_date(self, d):
                return run
        eng._store = _S()
        assert eng._day_replay_receipt() is None

    def _ref_store(self, tmp_path, state, day, *,
                   doc_scenario, ref_scenario, run_scenario=None):
        # Build a real-writer-shape ref + run for the given scenario set.
        import json, hashlib
        original = self._receipt()
        if doc_scenario is not None:
            original["scenario_id"] = doc_scenario
        real_path = state / "runs" / "replay-day-receipts" / "run-9.json"
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(original))
        real_sha = "sha256:" + hashlib.sha256(
            real_path.read_bytes()).hexdigest()
        ref = {"schema_version": "p11.paper-replay-day.v1", "run_id": "run-9",
               "path": "runs/replay-day-receipts/run-9.json",
               "sha256": real_sha, "market_date": day}
        if ref_scenario is not None:
            ref["scenario_id"] = ref_scenario
        run = {"run_id": "run-9", "market_date": day,
               "p11_replay_day_receipt_ref": ref}
        if run_scenario is not None:
            run["scenario_id"] = run_scenario
        class _S:
            def latest_run_for_date(self, d):
                return run
        eng = self._engine(tmp_path, state)
        eng._store = _S()
        return eng

    def test_writer_no_outer_scenario_legit_ref_reads(self, tmp_path):
        # 协调口径: real writer INPUT_BLOCKED runs often carry NO outer
        # scenario_id; the legal ref + original both declare spdb-test.
        # ref<->doc agree -> the receipt reads (was wrongly refused).
        state = tmp_path / "st"
        eng = self._ref_store(tmp_path, state, "2026-10-08",
                              doc_scenario="spdb-test",
                              ref_scenario="spdb-test",
                              run_scenario=None)
        doc = eng._day_replay_receipt()
        assert doc is not None
        assert doc["delivered"]["600000"]["entry_eligible"] is True
        assert doc["scenario_id"] == "spdb-test"

    def test_ref_scenario_differs_from_original_refused(self, tmp_path):
        # ref<->doc ALWAYS cross-check: a ref declaring scn-A bound to a
        # doc written under scn-B is refused, whatever the outer run has.
        state = tmp_path / "st"
        eng = self._ref_store(tmp_path, state, "2026-10-08",
                              doc_scenario="scn-B",
                              ref_scenario="scn-A",
                              run_scenario=None)
        assert eng._day_replay_receipt() is None

    def test_outer_scenario_bound_must_match_too(self, tmp_path):
        # When the outer run DOES bind a scenario, ref and doc must each
        # equal it - a doc/ref pair agreed on spdb-test but run bound to
        # scn-OTHER is refused.
        state = tmp_path / "st"
        eng = self._ref_store(tmp_path, state, "2026-10-08",
                              doc_scenario="spdb-test",
                              ref_scenario="spdb-test",
                              run_scenario="scn-OTHER")
        assert eng._day_replay_receipt() is None
        # same, run bound to spdb-test -> reads
        eng2 = self._ref_store(tmp_path, state, "2026-10-08",
                               doc_scenario="spdb-test",
                               ref_scenario="spdb-test",
                               run_scenario="spdb-test")
        assert eng2._day_replay_receipt() is not None

    def test_delivered_day_keeps_whole_day_block_reason(self):
        # 协调 R6 item 3: delivered N/M is real, but a whole-day block
        # (calendar_coverage_incomplete) must stay on the note.
        from quanthus.p12_replay.coverage_display import coverage_block
        r = self._receipt()
        r["day_block"] = {"reason": "calendar_coverage_incomplete",
                          "scope": "whole_day"}
        block = coverage_block(r)
        assert block["pool_count"] == 2           # N/M still real
        assert block["denominator_known"] if "denominator_known" in block else True
        assert block["day_block"]["reason"] == "calendar_coverage_incomplete"
        assert "calendar_coverage_incomplete" in block["note_text"] or "整日阻断" in block["note_text"]
        assert "2" in block["note_text"]

    def test_real_b451_clock_blocked_pool_counts(self):
        # 协调真回执对照: CN02 owner-e1-three-b451 day.json shape -
        # delivered dict, seam.day_blocked, disposition=input_not_ready,
        # NO top-level day_block; 三券 gates.events.reason=
        # "scan verdict at T: knowledge_after_t_close" (时钟阻断),
        # 5558 券按动作覆盖隔离 / excluded.  N must count only a
        # genuinely scanned-and-no-signal security, never the clock
        # blocks; M is the full delivered pool.
        from quanthus.p12_replay.coverage_display import coverage_block
        d = {
            "schema_version": "p11.paper-replay-day.v1",
            "market_date": "2026-10-08",
            "disposition": "input_not_ready",
            "delivered": {c: {"entry_eligible": True} for c in
                          ("600000", "000002", "002040", "300362", "600111")},
            "seam": {"day_blocked": True,
                     "evaluated_universe": ["600000", "000002", "002040",
                                            "300362", "600111"],
                     "decisions": [], "excluded": []},
            "diagnostics": [
                {"reason": "history_gate_unsatisfied: every market input "
                           "failed its history qualification",
                 "scope": "whole_day", "stage": "shared_prerequisite"},
            ],
            "source_closure": {
                "day_blocked": True,
                "evaluated_universe": ["600000", "000002", "002040",
                                       "300362", "600111"],
                "history_receipts": [
                    {"security_code": "600000", "status": "not_qualified",
                     "gates": {"events": {"status": "failed",
                        "reason": "scan verdict at T: knowledge_after_t_close"}}},
                    {"security_code": "000002", "status": "not_qualified",
                     "gates": {"events": {"status": "failed",
                        "reason": "scan verdict at T: knowledge_after_t_close"}}},
                    {"security_code": "002040", "status": "not_qualified",
                     "gates": {"events": {"status": "failed",
                        "reason": "scan verdict at T: knowledge_after_t_close"}}},
                    {"security_code": "600111", "status": "not_qualified",
                     "gates": {"events": {"status": "failed",
                        "reason": "no qualified event window in the 22-session scan"}}},
                    {"security_code": "300362", "status": "excluded",
                     "reason": "not_listed_on_signal_date"},
                ],
            },
        }
        block = coverage_block(d)
        # M = full delivered pool; N counts only the real scanned-no-signal
        # (600111).  三券时钟阻断 out of N, excluded out of N.
        assert block["pool_count"] == 5
        assert block["evaluated_count"] == 1
        by_code = {r["security_code"]: r for r in block["securities"]}
        assert by_code["600000"]["state"] == "blocked"
        assert "knowledge_after_t_close" in by_code["600000"]["scan_reason"]
        assert by_code["000002"]["state"] == "blocked"
        assert by_code["002040"]["state"] == "blocked"
        assert by_code["600111"]["state"] == "evaluated"
        assert by_code["300362"]["state"] == "pending"
        # whole-day input shortage surfaced from disposition/diagnostics
        # even without a top-level day_block.
        assert block["day_block"] is not None
        assert "history_gate_unsatisfied" in block["note_text"] or \
               "整日阻断" in block["note_text"]
