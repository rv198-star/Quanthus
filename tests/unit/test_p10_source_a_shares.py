"""A-share native units, bounded coverage and knowledge-time admission limits."""

import copy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest

from quanthus.p10_source_contracts import normalize, policy

CONFIG = Path(__file__).resolve().parents[2] / "config/pipelines/p10-supplement-sources.v4.json"
STAMP = datetime(2001, 2, 3, tzinfo=timezone.utc)


@pytest.fixture
def config():
    value = copy.deepcopy(policy(CONFIG))
    for spec in value["sources"]:
        if spec["kind"] == "a_share_margin":
            spec["sample_scope"].update(start="2001-02-01", end="2001-02-02")
        else:
            spec["sample_scope"]["trade_date"] = "2001-02-02"
    return value


def spec(config, kind):
    return next(x for x in config["sources"] if x["kind"] == kind)


def item(kind, **changes):
    fields = {
        "a_share_margin": {
            "RZYE": Decimal("100.125"), "RQYE": Decimal("10.25"),
            "RZRQYE": Decimal("110.375"), "RZMRE": Decimal("2.125"),
            "RZCHE": Decimal("3.125"), "RZJME": Decimal(-1),
            "RQYL": Decimal(200), "RQMCL": Decimal(0),
            "RQCHL": Decimal(100), "RQJMG": Decimal(-100),
        },
        "a_share_block_trade": {
            "DEAL_PRICE": Decimal("1.25"), "CLOSE_PRICE": Decimal("1.30"),
            "DEAL_VOLUME": Decimal(20000), "DEAL_AMT": Decimal("25000.5"),
            "PREMIUM_RATIO": Decimal("-0.038461538462"),
        },
    }
    return Obj(**{
        "security": "sh600519" if kind == "a_share_margin" else "sz000001",
        "name": "Synthetic", "session_date": date(2001, 2, 2),
        "source_fields": tuple(fields[kind].items()),
        "unqualified_fields": (("RZRQYECZ", "89.875"),),
        "reported_total": 68, "reported_pages": 34,
        "source_rank": 3, "buyer": "Synthetic Buyer", "seller": "Synthetic Seller",
        **changes,
    })


def normalized(config, kind, **changes):
    selected = spec(config, kind)
    provider, capability = selected["key"].split(":", 1)
    result = Obj(
        observation=Obj(request=Obj(provider=provider, capability=capability), acquired_at=STAMP),
        status=Obj(value="SUCCESS"), reason="SYNTHETIC_SOURCE_RECORDS",
        records=(item(kind, **changes),),
    )
    return normalize(result, selected, config)


def test_margin_yuan_and_shares_do_not_receive_display_scaling(config):
    value = normalized(config, "a_share_margin")
    row = value["records"][0]
    assert row["metrics"]["RZYE"]["value"] == "100.125"
    assert row["metrics"]["RZYE"]["unit"] == "CNY"
    assert row["metrics"]["RZJME"]["value"] == "-1"
    assert row["metrics"]["RQJMG"]["value"] == "-100"
    assert row["metrics"]["RQYL"]["unit"] == "SHARES"
    assert "RZRQYECZ" not in row["metrics"]
    assert row["unqualified_source_fields"] == {"RZRQYECZ": "89.875"}
    assert "BALANCE_DIFFERENCE_NOT_ADMITTED_AS_DAILY_CHANGE" in value["quality_flags"]


def test_block_api_shares_yuan_and_fraction_are_already_native(config):
    value = normalized(config, "a_share_block_trade")
    row = value["records"][0]
    assert row["metrics"]["DEAL_VOLUME"]["value"] == "20000"
    assert row["metrics"]["DEAL_VOLUME"]["unit"] == "SHARES"
    assert row["metrics"]["DEAL_AMT"]["value"] == "25000.5"
    assert row["metrics"]["DEAL_AMT"]["unit"] == "CNY"
    assert row["metrics"]["PREMIUM_RATIO"]["value"] == "-0.038461538462"
    assert row["metrics"]["PREMIUM_RATIO"]["unit"] == "FRACTION"
    assert not row["reported_amount_matches_price_times_shares"]
    assert "BLOCK_TRADE_REPORTED_AMOUNT_DIFFERS_FROM_PRICE_TIMES_SHARES" in value["quality_flags"]
    assert row["transaction_basis"].endswith("NOT_PORTFOLIO_FILL")


@pytest.mark.parametrize("kind", ["a_share_margin", "a_share_block_trade"])
def test_session_label_cannot_be_publication_clock_or_historical_pit(config, kind):
    value = normalized(config, kind)
    row = value["records"][0]
    assert row["source_clock"] is None and row["publication_at"] is None
    assert row["knowledge_from"] == STAMP.isoformat()
    assert value["source_latest_date"] == "2001-02-02"
    assert row["source_reported_total"] == 68 and row["source_returned_rows"] == 1
    assert not row["all_matching_rows_acquired"]
    assert not value["historical_pit_admission"] and not value["business_admission"]
    assert value["quality_ok"]
    assert "FIXED_SESSION_SAMPLE_NOT_LATEST_COVERAGE" in value["quality_flags"]


@pytest.mark.parametrize("kind", ["a_share_margin", "a_share_block_trade"])
def test_wrong_security_or_session_cannot_reuse_unit_contract(config, kind):
    for changes in ({"security": "AAPL"}, {"session_date": date(2001, 2, 4)}):
        with pytest.raises(ValueError, match="SOURCE_A_SHARE_.*_SCOPE_MISMATCH"):
            normalized(config, kind, **changes)


def test_margin_cannot_relabel_another_security(config):
    with pytest.raises(ValueError, match="SOURCE_A_SHARE_SECURITY_SCOPE_MISMATCH"):
        normalized(config, "a_share_margin", security="sh600000")


def test_new_source_fields_need_explicit_unit_contract(config):
    with pytest.raises(ValueError, match="SOURCE_A_SHARE_METRIC_SET_MISMATCH"):
        normalized(config, "a_share_margin", source_fields=(("RZYE", Decimal(1)),))


def test_future_session_is_not_admitted_even_inside_requested_window(config):
    spec(config, "a_share_block_trade")["sample_scope"]["trade_date"] = "2001-02-04"
    value = normalized(config, "a_share_block_trade", session_date=date(2001, 2, 4))
    assert not value["quality_ok"] and value["fallback_action"] == "ISOLATE_OPTIONAL_SOURCE"
    assert "SOURCE_DATE_IN_FUTURE" in value["quality_flags"]


def test_v4_keeps_owner_market_priority_and_explicit_bounded_purposes():
    value = policy(CONFIG)
    assert value["source_priority"] == {"a_shares": "HIGHEST", "hk_us": "LOW_NEW_EVIDENCE_ONLY"}
    assert value["capability_selection"] == "EXACT_DECLARED_SUBSET"
    assert len(value["sources"]) == 2 and value["budget"]["no_vendor_fallback"]
    assert value["watch"]["automatic_business_admission"] is False
