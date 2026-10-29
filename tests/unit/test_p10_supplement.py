"""Unit, clock, isolation, immutable rollback and probation acceptance contracts."""

import copy
import json
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest

from quanthus import p10_supplement as runner
from quanthus.p10_source_contracts import digest, metric, normalize, period, policy

CONFIG = Path(__file__).resolve().parents[2] / "config/pipelines/p10-supplement-sources.v1.json"
STAMP = datetime(2026, 10, 30, 0, tzinfo=timezone.utc)
REVISION = "a" * 40


@pytest.fixture
def config():
    return policy(CONFIG)


def spec(config, key):
    return next(x for x in config["sources"] if x["key"] == key)


def result(key, records=(), status="SUCCESS", at=STAMP):
    return Obj(
        observation=Obj(
            request=Obj(provider=key.split(":")[0], capability=key.split(":")[1]), acquired_at=at
        ),
        status=Obj(value=status),
        records=records,
        reason="SYNTHETIC_CONTRACT",
    )


@pytest.mark.parametrize(
    "series,text,marker,expected",
    [
        ("gdp", "2026年第1-3季度", "2026-09-01 00:00:00", ("2026-01-01", "2026-09-30")),
        ("gdp", "2026年第1-1季度", "2026-03-01 00:00:00", ("2026-01-01", "2026-03-31")),
        ("gdp", "2026年第1季度", "2026-03-01 00:00:00", ("2026-01-01", "2026-03-31")),
        ("m2", "2024年02月份", "2024-02-01 00:00:00", ("2024-02-01", "2024-02-29")),
    ],
)
def test_period_is_not_publication(series, text, marker, expected):
    assert period(series, text, marker) == expected


@pytest.mark.parametrize(
    "text,marker",
    [
        ("2026年第1-3季度", "2026-07-01 00:00:00"),
        ("2026年第3季度", "2026-09-01 00:00:00"),
        ("2026年第1-5季度", "2026-15-01 00:00:00"),
    ],
)
def test_ambiguous_or_conflicting_gdp_period_blocks(text, marker):
    with pytest.raises(ValueError):
        period("gdp", text, marker)


def test_currency_and_percent_scale_exactly_once(config):
    key = "eastmoney:macro.gdp"
    sample = Obj(
        series="gdp",
        period_text="2026年第1-3季度",
        report_date_text="2026-09-01 00:00:00",
        source_fields=(
            ("DOMESTICL_PRODUCT_BASE", Decimal("123.456789")),
            ("SUM_SAME", Decimal("5.20")),
        ),
    )
    row = normalize(result(key, (sample,)), spec(config, key), config)["records"][0]
    assert Decimal(row["metrics"]["DOMESTICL_PRODUCT_BASE"]["value"]) == Decimal("12345678900")
    assert Decimal(row["metrics"]["SUM_SAME"]["value"]) == Decimal("0.0520")
    assert row["metrics"]["SUM_SAME"]["native_value"] == "5.20"
    assert row["knowledge_from"] == STAMP.isoformat()
    assert row["publication_at"] is None and row["source_clock"] is None


def test_index_points_are_not_percentages(config):
    cpi = spec(config, "eastmoney:macro.cpi")
    pmi = spec(config, "eastmoney:macro.pmi")
    assert metric("101.3", cpi["fields"]["NATIONAL_BASE"])["value"] == "101.3"
    assert metric("50.1", pmi["fields"]["MAKE_INDEX"])["value"] == "50.1"
    assert metric("1.3", cpi["fields"]["NATIONAL_SAME"])["value"] == "0.013"


@pytest.mark.parametrize("value", [float("nan"), True, "NaN", "Infinity", "--"])
def test_invalid_units_never_become_zero(config, value):
    with pytest.raises(ValueError):
        metric(value, spec(config, "eastmoney:macro.gdp")["fields"]["SUM_SAME"])


def test_lpr_effective_date_does_not_infer_nine_am_release(config):
    key = "chinamoney:lpr.history"
    item = Obj(
        effective_date=date(2026, 10, 20),
        one_year_percent=Decimal("3.00"),
        five_year_percent=Decimal("3.5"),
    )
    row = normalize(result(key, (item,)), spec(config, key), config)["records"][0]
    assert row["metrics"]["one_year"]["value"] == "0.0300"
    assert row["effective_date"] == "2026-10-20"
    assert row["publication_at"] is None and row["source_clock"] is None


def test_disclosure_milliseconds_preserve_clock_and_future_flag(config):
    key = "cninfo:disclosure.index"
    item = Obj(
        source_timestamp_ms=1793318400123,
        announcement_id="test",
        security_code="600519",
        organization_id="org",
        title="synthetic",
        attachment_path="synthetic.pdf",
    )
    output = normalize(result(key, (item,)), spec(config, key), config)
    assert output["records"][0]["source_clock"] == "2026-10-30T00:00:00.123000+00:00"
    assert output["records"][0]["publication_at"] is None
    item.source_timestamp_ms += 600_000
    future = normalize(result(key, (item,)), spec(config, key), config)
    assert future["quality_ok"] is False and "SOURCE_CLOCK_IN_FUTURE" in future["quality_flags"]


def test_old_news_is_usable_only_for_archive_purpose(config):
    item = Obj(
        native_id="id",
        title="synthetic",
        url="https://example.com/test",
        source_ctime_text="1",
        source_created_at=STAMP - timedelta(days=10),
    )
    for key, usable in (("sina:news.roll.2516", False), ("sina:news.roll.2510", True)):
        output = normalize(result(key, (item,)), spec(config, key), config)
        assert output["quality_ok"] is usable
        assert output["historical_pit_admission"] is False


def test_empty_and_transport_failure_remain_distinct(config):
    key = "cnbc:news.rss"
    for status in ("EMPTY", "FAILED"):
        value = normalize(result(key, status=status), spec(config, key), config)
        assert value["status"] == status and not value["quality_ok"]
        assert value["fallback_action"] == "ISOLATE_OPTIONAL_SOURCE"


@pytest.fixture
def small_store(config, monkeypatch, tmp_path):
    # Real library envelope types, with synthetic parsers and no market/network reads.
    library = pytest.importorskip("astock.contracts")
    selected = [spec(config, "chinamoney:lpr.history"), spec(config, "eastmoney:macro.cpi")]
    config = copy.deepcopy(config)
    config["sources"] = selected
    contracts = {}
    for entry in selected:
        key = entry["key"]
        request = library.SourceRequest(*key.split(":"), "GET", "https://example.com/" + key)
        item = (
            Obj(
                effective_date=date(2026, 10, 20),
                one_year_percent=Decimal("3"),
                five_year_percent=Decimal("3.5"),
            )
            if entry["kind"] == "lpr"
            else Obj(
                series="cpi",
                period_text="2026年09月份",
                report_date_text="2026-09-01 00:00:00",
                source_fields=(
                    ("NATIONAL_BASE", Decimal("101")),
                    ("NATIONAL_SAME", Decimal("1")),
                    ("NATIONAL_SEQUENTIAL", Decimal("0.1")),
                ),
            )
        )

        def parse(raw, item=item):
            return (
                library.FetchResult(raw, library.Status.FAILED, raw.error)
                if raw.error
                else (library.FetchResult(raw, library.Status.SUCCESS, "SYNTHETIC", (item,)))
            )

        contracts[key] = (request, parse)
    monkeypatch.setattr(runner, "library_contracts", lambda _: contracts)
    state = {"at": STAMP, "calls": 0, "fail": None}

    def fetch(request, budget):
        state["calls"] += 1
        error = "REQUEST_TIMEOUT" if state["fail"] == request.provider else None
        return library.RawObservation(
            request, state["at"], b"synthetic", None if error else 200, not error, error
        )

    def run(disabled=()):
        return runner.cycle(
            tmp_path / "evidence",
            config,
            REVISION,
            fetcher=fetch,
            clock=lambda: state["at"],
            disabled=disabled,
        )

    return tmp_path / "evidence", config, state, run


def test_daily_restart_reuses_verified_product_without_second_fetch(small_store):
    root, config, state, run = small_store
    first = run()
    assert first["network_requests"] == 2
    again = run()
    assert again["disposition"] == "REUSED" and again["network_requests"] == 0
    assert state["calls"] == 2
    ledger = json.loads((root / "candidates.json").read_bytes())
    assert {x["distinct_success_days"] for x in ledger["candidates"]} == {1}


def test_optional_failure_does_not_reuse_prior_good_or_block_other_source(small_store):
    root, config, state, run = small_store
    previous = run()
    state.update(at=STAMP + timedelta(days=1), fail="chinamoney")
    failed = run()
    manifest = runner.verified_run(root / "runs" / failed["run_id"])
    key = "chinamoney:lpr.history"
    value = json.loads((root / "runs" / failed["run_id"] / manifest["sources"][key]).read_bytes())
    assert not value["current_available"] and not value["previous_good_reused"]
    assert value["previous_good_run"] == previous["run_id"]
    ledger = json.loads((root / "candidates.json").read_bytes())
    assert [x["status"] for x in ledger["candidates"]] == ["HOLD", "WATCHING"]
    with pytest.raises(ValueError, match="CURRENT_OBSERVATION_UNAVAILABLE"):
        runner.read_observation(
            root, failed["run_id"], key, state["at"].isoformat(), policy_sha256=digest(config)
        )


def test_rollback_disable_is_auditable_and_keeps_previous_snapshots(small_store):
    root, config, state, run = small_store
    previous = run()
    before = (root / "runs" / previous["run_id"] / "manifest.json").read_bytes()
    state["at"] += timedelta(days=1)
    rollback = run(disabled=("chinamoney:lpr.history",))
    assert rollback["network_requests"] == 1
    assert (root / "runs" / previous["run_id"] / "manifest.json").read_bytes() == before
    ledger = json.loads((root / "candidates.json").read_bytes())
    assert ledger["candidates"][0]["last_quality"]["reason"] == "SOURCE_DISABLED_ROLLBACK"


def test_snapshot_reader_checks_actual_knowledge_clock_policy_and_tampering(small_store):
    root, config, state, run = small_store
    output = run()
    args = (root, output["run_id"], "chinamoney:lpr.history")
    with pytest.raises(ValueError, match="NOT_KNOWN_AT_CUTOFF"):
        runner.read_observation(
            *args, (STAMP - timedelta(seconds=1)).isoformat(), policy_sha256=digest(config)
        )
    with pytest.raises(ValueError, match="READ_POLICY_MISMATCH"):
        runner.read_observation(*args, STAMP.isoformat(), policy_sha256="0" * 64)
    assert (
        runner.read_observation(*args, STAMP.isoformat(), policy_sha256=digest(config))[
            "business_admission"
        ]
        is False
    )
    body = next((root / "runs" / output["run_id"]).glob("*.bin"))
    body.write_bytes(b"changed")
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        run()


def test_probation_requires_distinct_days_and_recent_consecutive_successes(small_store):
    root, config, state, run = small_store
    run()
    state["at"] += timedelta(days=7)
    ledger = runner._ledger(root, config, state["at"])
    assert {x["status"] for x in ledger["candidates"]} == {"HOLD_REVIEW_DUE"}
    for offset in (5, 6, 7):
        state["at"] = STAMP + timedelta(days=offset)
        run()
    ledger = runner._ledger(root, config, state["at"])
    assert {x["status"] for x in ledger["candidates"]} == {"REVIEW_DUE"}
    assert all(not x["business_admission"] for x in ledger["candidates"])
    state["at"] += timedelta(days=2)
    assert {x["status"] for x in runner._ledger(root, config, state["at"])["candidates"]} == {
        "HOLD_REVIEW_DUE"
    }


def test_existing_nonempty_or_symlink_root_fails_closed(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("protected")
    with pytest.raises(ValueError, match="EMPTY_NEW_ROOT_REQUIRED"):
        runner._initialize(existing)
    linked = tmp_path / "link"
    linked.symlink_to(existing)
    with pytest.raises(ValueError, match="ISOLATED_ABSOLUTE_ROOT_REQUIRED"):
        runner._initialize(linked)


def test_fund_nav_currency_scope_status_and_valuation_clock(config):
    key = "eastmoney:fund.nav"
    item = Obj(
        fund_code="000001",
        valuation_date=date(2026, 10, 29),
        unit_nav_native=Decimal("1.0023"),
        accumulated_nav_native=Decimal("2.5000"),
        daily_change_percent=Decimal("-0.17"),
        subscription_status="OPEN",
        redemption_status="CLOSED",
        dividend_text="",
        identity_basis="REQUEST_BOUND_NO_RESPONSE_ECHO",
    )
    output = normalize(result(key, (item,)), spec(config, key), config)
    row = output["records"][0]
    assert row["metrics"]["unit_nav"]["unit"] == "CNY_PER_FUND_UNIT"
    assert row["metrics"]["daily_change"]["value"] == "-0.0017"
    assert row["source_clock"] is None and row["publication_at"] is None
    assert output["source_latest_date"] == "2026-10-29"
    assert output["source_latest_date_age_days"] == 1
    assert "FUND_IDENTITY_REQUEST_BOUND_NO_RESPONSE_ECHO" in output["quality_flags"]
    item.fund_code = "000002"
    with pytest.raises(ValueError, match="FUND_SCOPE_MISMATCH"):
        normalize(result(key, (item,)), spec(config, key), config)


def test_expired_watch_stops_and_restart_cannot_renew_budget(config, tmp_path, monkeypatch):
    state = {"at": STAMP, "calls": 0}
    monkeypatch.setattr(runner, "utcnow", lambda: state["at"])

    def advance(seconds):
        state["at"] += timedelta(days=1)

    def fake_cycle(*args, **kwargs):
        state["calls"] += 1
        return {"synthetic": True}

    monkeypatch.setattr(runner.time, "sleep", advance)
    monkeypatch.setattr(runner, "cycle", fake_cycle)
    root = tmp_path / "watch"
    runner.watch(root, config, REVISION, ())
    receipt = json.loads((root / "watch.json").read_bytes())
    assert receipt["state"] == "STOPPED_REVIEW_DUE" and state["calls"] == 7
    until = receipt["until"]
    runner.watch(root, config, REVISION, ())
    assert json.loads((root / "watch.json").read_bytes())["until"] == until
    assert state["calls"] == 7
    with pytest.raises(ValueError, match="CHANGED_CONTRACT_REQUIRES_NEW_ROOT"):
        runner.watch(root, config, "b" * 40, ())


def _blocked_worker(sender, request, max_bytes):
    time.sleep(60)


def test_outer_budget_terminates_a_stuck_source_before_any_socket_call(monkeypatch):
    library = pytest.importorskip("astock.contracts")
    monkeypatch.setattr(runner, "_fetch_child", _blocked_worker)
    request = library.SourceRequest("test", "test", "GET", "https://example.com/")
    started = time.monotonic()
    value = runner.bounded_fetch(request, {"max_response_bytes": 256, "source_wall_seconds": 0.05})
    assert value.error == "SOURCE_OPERATION_TIMEOUT" and value.body == b""
    assert time.monotonic() - started < 3


def test_installed_library_must_match_the_full_module_closure(config, monkeypatch):
    pytest.importorskip("astock")
    from quanthus.p10_source_contracts import library_contracts

    assert len(library_contracts(config)) == 15
    changed = copy.deepcopy(config)
    changed["library"]["module_sha256"]["funds.py"] = "0" * 64
    with pytest.raises(ValueError, match="LIBRARY_CONTENT_MISMATCH"):
        library_contracts(changed)
