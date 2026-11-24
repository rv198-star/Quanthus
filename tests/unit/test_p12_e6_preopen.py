"""E6 pre-open check: stock-pool snapshot, fact resolution, verify-only run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class _History:
    orders: tuple = ()


@dataclass(frozen=True)
class _Quote:
    open: Decimal | None = None
    source: str = "tencent"
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
    q = _Quote(open=Decimal("10.9"), quote_time=_at(9, 25), received_at=_at(9, 25))
    v = preopen.resolve_auction(q, "600000", _at(9, 20))
    assert v.status == "verified"
    assert v.source == "tencent"


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
    )
    r = receipt["plans"][0]
    assert r["executable"] is False
    assert "PREOPEN_SUSPENSION_UNAVAILABLE" in r["reason_codes"]
    assert receipt["summary"]["blocked"] == 1


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
    q = _Quote(open=Decimal("11.50"), quote_time=_at(9, 25), received_at=_at(9, 25))
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