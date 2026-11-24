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
    '''R5: 当日覆盖展示 - the same-run rule receipt drives N/M and the
    per-security verdict + scan reason; the synthetic phase shows pool-only.'''

    def test_real_receipt_rows_and_counts(self):
        from quanthus.p12_replay.coverage_display import coverage_block

        receipt = {
            "delivered": {
                "600000": {"bar_rows": 61, "window_sessions": 61,
                           "gap_sessions": 0, "entry_eligible": True},
                "000002": {"bar_rows": 61, "window_sessions": 61,
                           "gap_sessions": 0, "entry_eligible": True},
                "601390": {"bar_rows": 40, "window_sessions": 61,
                           "gap_sessions": 21, "entry_eligible": False},
            },
            "seam": {
                "evaluated_universe": ["600000", "000002"],
                "decisions": [
                    {"security_code": "600000", "disposition": "ENTRY",
                     "reason": "NEW_ENTRY_SIGNAL"},
                    {"security_code": "000002", "disposition": "REVIEW",
                     "reason": "SOME_CODE"},
                ],
                "excluded": [],
            },
            "source_closure": {
                "held_decisions": [
                    {"security_code": "600000", "action": "held",
                     "reason": "HELD_OK"},
                ],
            },
            "excluded_security_codes": ["300999"],
            "coverage_exclusions": [
                {"security_code": "601390", "reason": "ACTION_COVERAGE_UNKNOWN"},
            ],
            "as_observed": {"note": "x"},
        }
        block = coverage_block(receipt)
        assert block["pool_count"] == 3
        assert block["evaluated_count"] == 2
        assert block["pending_count"] == 1
        assert block["note_text"].startswith("本日可评估 2 只 / 策略池 3 只")
        assert "其余 1 只" in block["note_text"]
        rows = {r["security_code"]: r for r in block["securities"]}
        assert rows["600000"]["evaluated"] is True
        assert rows["600000"]["verdict"] == "新建信号"
        assert rows["000002"]["verdict"] == "待复核"
        # The coverage-isolated member is pending with a clearance reason.
        assert rows["601390"]["evaluated"] is False
        assert rows["601390"]["state"] == "pending"
        assert rows["601390"]["scan_reason"]

    def test_zero_signal_day_still_lists_evaluated(self):
        from quanthus.p12_replay.coverage_display import coverage_block

        receipt = {
            "delivered": {
                "600000": {"entry_eligible": True},
                "000002": {"entry_eligible": True},
            },
            "seam": {
                "evaluated_universe": ["600000", "000002"],
                "decisions": [],
                "excluded": [],
            },
            "source_closure": {"held_decisions": []},
            "coverage_exclusions": [],
        }
        block = coverage_block(receipt)
        # Zero signals: N=2 evaluated, every one still listed.
        assert block["evaluated_count"] == 2
        assert len(block["securities"]) == 2
        assert all(r["evaluated"] for r in block["securities"])
        assert all(r["verdict"] is None for r in block["securities"])

    def test_synthetic_returns_pool_only(self):
        from quanthus.p12_replay.coverage_display import (
            coverage_block,
            pool_only_block,
        )
        assert coverage_block(None) is None
        block = pool_only_block(3, delivered_codes=["600000", "000002", "601390"])
        assert block["pool_count"] == 3
        assert block["evaluated_count"] is None
        assert len(block["securities"]) == 3
        assert all(r["evaluated"] is None for r in block["securities"])
        assert "待真实规则接入" in block["note_text"]
