"""E6 pre-open check: stock-pool snapshot, fact resolution, verify-only run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.product_daily.application import preopen

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class _Plan:
    plan_id: str = "p-1"
    security_code: str = "600000"
    side: str = "buy"
    current_qty: int = 0
    target_qty: int = 400
    delta_qty: int = 400
    price_cap: Decimal = Decimal("10.80")
    estimated_fee: Decimal = Decimal("0.00")
    depends_on_preopen: tuple = ()


@dataclass(frozen=True)
class _Package:
    package_id: str = "pkg-2026-11-20"
    plans: tuple = ()


@dataclass(frozen=True)
class _Pos:
    security_code: str
    quantity: int
    security_name: str | None = None


@dataclass(frozen=True)
class _Projection:
    cash: Decimal = Decimal("100000.00")
    revision: int = 3
    positions: tuple = ()

    def quantity(self, code: str) -> int:
        return next((p.quantity for p in self.positions if p.security_code == code), 0)


@dataclass(frozen=True)
class _Order:
    security_code: str
    side: str
    hold_qty: int = 0
    hold_amount: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class _History:
    orders: tuple = ()


@dataclass(frozen=True)
class _Quote:
    open: Decimal | None = None
    source: str = "tencent"
    security_code: str = "600000"
    status: str = "live"
    stale: bool = False
    quote_time: datetime | None = None
    received_at: datetime | None = None


class _Quotes:
    def __init__(self, mapping=None):
        self._m = mapping or {}

    def quote(self, code):
        return self._m.get(code)


def _at(h, m=0):
    return datetime(2026, 11, 20, h, m, tzinfo=SHANGHAI)


# ---- stock pool snapshot ---------------------------------------------------

def test_pool_unions_buy_candidates_and_positions():
    pkg = _Package(plans=(
        _Plan(plan_id="p-buy", security_code="600000", side="buy", delta_qty=400),
        _Plan(plan_id="p-sell", security_code="000001", side="sell", delta_qty=200),
    ))
    positions = (
        _Pos("000001", 200, "平安银行"),
        _Pos("600000", 100, "浦发银行"),
    )
    snap = preopen.stock_pool_snapshot(package=pkg, positions=positions, as_of=_at(8, 55))
    assert snap["schema_version"] == preopen.SNAPSHOT_SCHEMA
    assert snap["trading_date"] == "2026-11-20"
    assert snap["sha256"]
    codes = {s["security_code"] for s in snap["securities"]}
    assert codes == {"600000", "000001"}
    buy = next(s for s in snap["securities"] if s["security_code"] == "600000")
    kinds = {src["kind"] for src in buy["sources"]}
    # both a buy candidate and a held position keep every source
    assert kinds == {"buy_candidate", "held_position"}
    sell = next(s for s in snap["securities"] if s["security_code"] == "000001")
    sk = {src["kind"] for src in sell["sources"]}
    assert sk == {"exit_intent", "held_position"}


def test_pool_empty_when_no_package_no_positions():
    snap = preopen.stock_pool_snapshot(package=None, positions=(), as_of=_at(8, 55))
    assert snap["securities"] == []


def test_write_pool_snapshot_path_and_sha(tmp_path):
    snap = preopen.stock_pool_snapshot(package=None, positions=(), as_of=_at(8, 55))
    path = preopen.write_stock_pool_snapshot(tmp_path, snap)
    assert path.name == "2026-11-20.json"
    assert path.parent.name == "preopen-pool"
    import json
    on_disk = json.loads(path.read_text())
    assert on_disk["sha256"] == snap["sha256"]


# ---- fact resolution -------------------------------------------------------

def test_suspension_always_unavailable_until_named_supply():
    v = preopen.resolve_suspension(_Quote(), "600000", _at(9, 20))
    assert v.status == "unavailable"
    assert v.fact == preopen.FACT_SUSPENSION


def test_limit_price_always_unavailable_until_named_supply():
    v = preopen.resolve_limit_price(_Quote(), "600000", _at(9, 20))
    assert v.status == "unavailable"


def test_auction_unavailable_before_window():
    # 09:00 has no auction print; never pass it as verified
    v = preopen.resolve_auction(_Quote(open=Decimal("10.9")), "600000", _at(9, 0))
    assert v.status == "unavailable"


def test_auction_verified_with_open_print_in_window():
    # A print already observed AND received at the check instant verifies.
    q = _Quote(open=Decimal("10.9"), quote_time=_at(9, 20), received_at=_at(9, 20))
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "verified"
    assert v.source == "tencent"


def test_auction_future_received_at_is_unverified():
    # E6-02 regression: at 09:20 a print only received at 09:25 must not
    # read as verified (PIT: never back-read a later tick).
    q = _Quote(open=Decimal("10.9"), quote_time=_at(9, 25), received_at=_at(9, 25))
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "unavailable"


def test_auction_future_quote_time_is_unverified():
    q = _Quote(open=Decimal("10.9"), quote_time=_at(9, 25), received_at=_at(9, 20))
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "unavailable"


def test_auction_wrong_security_is_unverified():
    q = _Quote(open=Decimal("10.9"), security_code="000001",
               quote_time=_at(9, 20), received_at=_at(9, 20))
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "unavailable"


def test_auction_prior_session_is_unverified():
    prior = _at(9, 20) - timedelta(days=1)
    q = _Quote(open=Decimal("10.9"), quote_time=prior, received_at=prior)
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "unavailable"


def test_auction_stale_surface_is_unverified():
    q = _Quote(open=Decimal("10.9"), status="stale", stale=True,
               quote_time=_at(9, 20), received_at=_at(9, 20))
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "unavailable"


def test_auction_unavailable_without_open_print():
    v = preopen.resolve_auction(_Quote(open=None), "600000", _at(9, 20))
    assert v.status == "unavailable"


# ---- run check -------------------------------------------------------------

def test_buy_blocked_when_dependent_fact_missing():
    plan = _Plan(depends_on_preopen=(preopen.FACT_SUSPENSION,))
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(),
        history=_History(),
        quotes=_Quotes({"600000": _Quote()}),
        now=_at(9, 20),
        account_mode="real",
    )
    r = receipt["plans"][0]
    assert r["executable"] is False
    assert "PREOPEN_SUSPENSION_UNAVAILABLE" in r["reason_codes"]
    assert receipt["summary"]["blocked"] == 1


def test_buy_pending_unverified_in_simulation():
    # P11 v0.2 sec.5: a simulated buy with an UNKNOWN pre-open fact stays
    # confirmable-pending under the typed alert, never written as verified.
    plan = _Plan(depends_on_preopen=(preopen.FACT_SUSPENSION,))
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(),
        history=_History(),
        quotes=_Quotes({"600000": _Quote()}),
        now=_at(9, 20),
        account_mode="simulation",
    )
    r = receipt["plans"][0]
    assert r["executable"] is True
    assert "PREOPEN_SUSPENSION_UNAVAILABLE" in r["reason_codes"]
    assert "preopen_unverified_alert" in r["reason_codes"]
    assert receipt["summary"]["blocked"] == 0


def test_sell_proceeds_with_alert_when_fact_missing():
    plan = _Plan(side="sell", delta_qty=200, depends_on_preopen=(preopen.FACT_SUSPENSION,))
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(positions=(_Pos("600000", 400),)),
        history=_History(),
        quotes=_Quotes({"600000": _Quote()}),
        now=_at(9, 20),
        sellable_for=lambda c: 400,
    )
    r = receipt["plans"][0]
    # sell/reduce is never blocked by a missing pre-open fact; it alerts instead
    assert r["executable"] is True
    assert "preopen_unverified_alert" in r["reason_codes"]
    assert receipt["summary"]["sell_alerts"] == 1
    assert receipt["summary"]["blocked"] == 0


def test_buy_recompute_down_on_reduced_cash():
    plan = _Plan(target_qty=400, delta_qty=400, price_cap=Decimal("10.80"))
    # cash affords only ~300 shares -> whole-lot recompute to 300, not a buy
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(cash=Decimal("3300.00")),
        history=_History(),
        quotes=_Quotes({"600000": _Quote()}),
        now=_at(9, 20),
    )
    r = receipt["plans"][0]
    assert r["executable"] is False
    assert r["recheck_quantity"] == 300
    assert "cash_reduced" in r["reason_codes"]


def test_buy_blocked_when_auction_above_price_cap():
    plan = _Plan(price_cap=Decimal("10.80"))
    # a qualified (already observed+received) print above the cap still fails
    q = _Quote(open=Decimal("11.50"), quote_time=_at(9, 18), received_at=_at(9, 18))
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(),
        history=_History(),
        quotes=_Quotes({"600000": q}),
        now=_at(9, 20),
    )
    r = receipt["plans"][0]
    assert r["executable"] is False
    assert "auction_above_price_cap" in r["reason_codes"]

    # a print stamped in the future of the check instant is NOT used
    future = _Quote(open=Decimal("11.50"), quote_time=_at(9, 25), received_at=_at(9, 25))
    receipt2 = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(),
        history=_History(),
        quotes=_Quotes({"600000": future}),
        now=_at(9, 20),
    )
    assert "auction_above_price_cap" not in receipt2["plans"][0]["reason_codes"]


def test_check_marks_window_and_never_mints_signal():
    plan = _Plan()
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)),
        projection=_Projection(),
        history=_History(),
        quotes=_Quotes({"600000": _Quote()}),
        now=_at(9, 20),
    )
    assert receipt["in_window"] is True
    assert receipt["check"] == "preopen"
    assert receipt["sha256"]
    # a clean buy stays executable; no new field fabricates a signal
    assert receipt["plans"][0]["executable"] is True


def test_check_outside_window_flagged():
    receipt = preopen.run_check(
        package=_Package(plans=()),
        projection=_Projection(),
        history=_History(),
        quotes=_Quotes({}),
        now=_at(10, 0),
    )
    assert receipt["in_window"] is False


def test_attach_check_to_receipt():
    base = {"schema_version": "quanthus.p12.daily-run.v1", "status": "PUBLISHED"}
    check = {"check": "preopen", "trading_date": "2026-11-20"}
    merged = preopen.attach_check_to_receipt(base, check)
    assert merged["preopen_check"]["check"] == "preopen"
    assert merged["status"] == "PUBLISHED"

# ---- service integration (synthetic app) -------------------------------------

def _synthetic_app(tmp_path, now_box):
    from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
    from quanthus.platform.p12.assembly import synthetic_applications
    from quanthus.policy import find_project_root

    fixture = load_synthetic_fixture(
        find_project_root() / "config/p12/synthetic-desktop.v1.json"
    )
    from datetime import timezone as _tz
    def now():
        return now_box[0].astimezone(_tz.utc).astimezone()
    apps = synthetic_applications(state_dir=tmp_path, fixture=fixture, clock=now)
    return next(iter(apps.values()))


def test_service_preopen_pool_runs(tmp_path):
    now_box = [_at(8, 55)]
    app = _synthetic_app(tmp_path, now_box)
    snap = app.preopen_pool(app.scope, as_of=_at(8, 55))
    assert snap["schema_version"] == preopen.SNAPSHOT_SCHEMA
    assert "securities" in snap and "sha256" in snap


def test_service_preopen_check_runs(tmp_path):
    now_box = [_at(9, 20)]
    app = _synthetic_app(tmp_path, now_box)
    receipt = app.preopen_check(app.scope, as_of=_at(9, 20))
    assert receipt["check"] == "preopen"
    assert receipt["schema_version"] == preopen.CHECK_SCHEMA
    assert receipt["in_window"] is True
    assert receipt["sha256"]


def test_service_write_pool_persists_when_store_root(tmp_path):
    now_box = [_at(8, 55)]
    app = _synthetic_app(tmp_path, now_box)
    # FixtureDecisionSource has no _store; give it one pointing at a temp root.
    class _Store:
        _root = tmp_path / "state"
    app._decisions._store = _Store()
    path = app.write_preopen_pool(app.scope, as_of=_at(8, 55))
    assert path.name == "2026-11-20.json"
    assert path.exists()

# ---- E6-01 merged pool + consumption ----------------------------------------

def test_merge_pools_unions_all_accounts():
    pkg = _Package(plans=(_Plan(plan_id="p-buy", security_code="600000"),))
    a = preopen.stock_pool_snapshot(
        package=pkg, positions=(_Pos("600000", 100), _Pos("000001", 50)), as_of=_at(8, 55)
    )
    b = preopen.stock_pool_snapshot(
        package=pkg, positions=(_Pos("000002", 70),), as_of=_at(8, 55)
    )
    merged = preopen.merge_stock_pool_snapshots((a, b), _at(8, 55))
    codes = {r["security_code"] for r in merged["securities"]}
    assert codes == {"600000", "000001", "000002"}
    row = next(r for r in merged["securities"] if r["security_code"] == "600000")
    kinds = {s["kind"] for s in row["sources"]}
    assert "buy_candidate" in kinds and "held_position" in kinds


def test_load_pool_verifies_date_and_sha(tmp_path):
    snap = preopen.stock_pool_snapshot(
        package=_Package(plans=(_Plan(),)), positions=(_Pos("600000", 100),), as_of=_at(8, 55)
    )
    path = preopen.write_stock_pool_snapshot(tmp_path, snap)
    ok = preopen.load_stock_pool_snapshot(tmp_path, _at(9, 20).date())
    assert ok["trading_date"] == "2026-11-20"
    # tamper the file -> hash mismatch
    import json as _json
    body = _json.loads(path.read_text())
    body["trading_date"] = "2026-11-19"
    path.write_text(_json.dumps(body))
    try:
        preopen.load_stock_pool_snapshot(tmp_path, _at(9, 20).date())
        raise AssertionError("expected PoolError")
    except preopen.PoolError as exc:
        assert exc.code in ("POOL_CORRUPT", "POOL_STALE")


def test_load_pool_missing_raises(tmp_path):
    try:
        preopen.load_stock_pool_snapshot(tmp_path, _at(9, 20).date())
        raise AssertionError("expected POOL_MISSING")
    except preopen.PoolError as exc:
        assert exc.code == "POOL_MISSING"


# ---- E6-04 fee + reserved cash ---------------------------------------------

def test_check_buy_includes_fee_and_reserved():
    # cash 4320 covers 400 shares but not after fee 5 and a 3000 open-buy hold.
    plan = _Plan(target_qty=400, delta_qty=400,
                 price_cap=Decimal("10.80"), estimated_fee=Decimal("5.00"))
    proj = _Projection(cash=Decimal("4320.00"))
    hist = _History(orders=(_Order("000002", "buy", hold_amount=Decimal("3000.00")),))
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)), projection=proj, history=hist,
        quotes=_Quotes(), now=_at(9, 20), sellable_for=lambda c: 0,
        account_mode="simulation",
    )
    row = receipt["plans"][0]
    assert row["executable"] is False
    # 4320 - 3000 reserved - 5 fee = 1315 / 10.80 = 121 -> 100 shares
    assert row["recheck_quantity"] == 100
    assert "cash_reduced" in row["reason_codes"]


def test_check_buy_zero_qty_not_ordered():
    plan = _Plan(target_qty=400, delta_qty=400,
                 price_cap=Decimal("10.80"), estimated_fee=Decimal("0.00"))
    proj = _Projection(cash=Decimal("500.00"))
    receipt = preopen.run_check(
        package=_Package(plans=(plan,)), projection=proj, history=_History(),
        quotes=_Quotes(), now=_at(9, 20), sellable_for=lambda c: 0,
        account_mode="simulation",
    )
    row = receipt["plans"][0]
    assert row["executable"] is False
    assert row["recheck_quantity"] == 0


# ---- E6-06 pending_reason persistence ---------------------------------------

def test_pending_reason_persisted_and_cleared(tmp_path):
    import runpy
    from types import SimpleNamespace as N

    from quanthus.p12_paper.application import PaperAccount, PaperOrderStore
    from quanthus.p12_paper.exec_rule import OrderCommand
    from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
    from quanthus.platform.p12.assembly import synthetic_applications

    t = runpy.run_path(
        "/Users/william/Projects/Github/Quanthus/artifacts/worktrees/"
        "p12-d1b-e6-preopen/tests/unit/test_p12_paper_trading.py"
    )
    tm = datetime(2026, 11, 20, 9, 30, tzinfo=SHANGHAI)
    app = synthetic_applications(
        state_dir=tmp_path, fixture=load_synthetic_fixture(t["FIXTURE"]), clock=lambda: tm
    )["SYN-P12-A"]
    reads = []

    class Facade:
        scope = N(account_id=app.scope.account_id, mode="simulation")
        def __getattr__(self, k):
            return getattr(app, k)
        def paper_current_plan(self, pid):
            return N(plan_id=pid, side="buy", security_code="600000",
                     delta_qty=100, price_cap=Decimal("10.80"),
                     depends_on_preopen=("LIMIT_PRICE",))
        def preopen_fact_for(self, code, fact):
            reads.append((code, fact))
            return None

    paper = PaperAccount(
        app=Facade(),
        store=PaperOrderStore(tmp_path / "b.sqlite", clock=lambda: tm),
        quotes=t["_quotes"](lambda: tm, [t["_quote"]("10.66", tm)]),
        now=lambda: tm,
    )
    cmd = OrderCommand("plan:p-1", "buy", "600000", 100, Decimal("10.80"))
    out = paper.submit(cmd)
    assert out.status == "open"
    row = paper._store.get(cmd.order_id, app.scope.account_id)
    assert row.pending_reason == "PREOPEN_LIMIT_PRICE_UNAVAILABLE"
    # restart -> same store, alert survives the reload and re-submit echoes it
    paper2 = PaperAccount(
        app=Facade(),
        store=PaperOrderStore(tmp_path / "b.sqlite", clock=lambda: tm),
        quotes=t["_quotes"](lambda: tm, [t["_quote"]("10.66", tm)]),
        now=lambda: tm,
    )
    again = paper2.submit(cmd)
    assert again.status == "open"
    assert again.reason_code == "PREOPEN_LIMIT_PRICE_UNAVAILABLE"


def test_service_merged_pool_and_stale_consumption(tmp_path):
    import json as _json
    now_box = [_at(9, 20)]
    app = _synthetic_app(tmp_path, now_box)
    class _Store:
        _root = tmp_path / "state"
    app._decisions._store = _Store()
    # build one pool body per account (one app here) and merge
    body = app.preopen_pool(app.scope, as_of=_at(8, 55))
    path = app.write_preopen_pool_merged((body,), as_of=_at(8, 55))
    assert path.exists()
    ok = preopen.load_stock_pool_snapshot(tmp_path / "state", _at(9, 20).date())
    assert ok["trading_date"] == "2026-11-20"
    # corrupt it -> the service check keeps the pool unavailable
    d = _json.loads(path.read_text())
    d["trading_date"] = "2026-11-19"
    path.write_text(_json.dumps(d))
    receipt = app.preopen_check(app.scope, as_of=_at(9, 20))
    assert receipt["pool_status"].startswith("unavailable")
