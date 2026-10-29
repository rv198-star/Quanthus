"""New source-purpose, monetary units, date ambiguity and explicit subset contracts."""

import copy
import importlib.metadata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest

from quanthus.p10_source_contracts import library_contracts, normalize, policy

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/pipelines/p10-supplement-sources.v2.json"
STAMP = datetime(2001, 2, 3, 0, tzinfo=timezone.utc)


def result(key, item):
    return Obj(
        observation=Obj(
            request=Obj(provider=key.split(":")[0], capability=key.split(":")[1]),
            acquired_at=STAMP,
        ),
        status=Obj(value="SUCCESS"),
        records=(item,),
        reason="SYNTHETIC_CONTRACT",
    )


def normalized(key, item):
    config = policy(CONFIG)
    spec = next(x for x in config["sources"] if x["key"] == key)
    return normalize(result(key, item), spec, config)


def flow(**changes):
    return Obj(
        **{
            "security": "sh510300",
            "name": "Synthetic ETF",
            "source_timestamp_seconds": int(STAMP.timestamp()),
            "amounts_native": (("f62", Decimal("123456.78")),),
            "percentages_native": (("f184", Decimal("1.25")),),
            **changes,
        }
    )


def bond(**changes):
    return Obj(
        **{
            "bond_code": "123001",
            "name": "Synthetic bond",
            "bond_type": "C",
            "stock_code": "300001",
            "price_native": Decimal("111.123"),
            "conversion_price_native": Decimal("9.50"),
            "stock_price_native": Decimal("10.55"),
            "last_time_text": "15:00:01",
            "listing_date": None,
            "source_context_date": STAMP.date(),
            "guest_prompt": "Anonymous partial list",
            **changes,
        }
    )


def test_etf_cny_and_percentage_have_different_exact_scales():
    value = normalized("eastmoney:etf.trading_flow.snapshot", flow())
    row = value["records"][0]
    assert row["metrics"]["f62"]["value"] == "123456.78"
    assert row["metrics"]["f62"]["unit"] == "CNY"
    assert row["metrics"]["f184"]["value"] == "0.0125"
    assert "NOT_CREATION_REDEMPTION" in row["flow_basis"]
    assert value["quality_ok"]


def test_etf_source_epoch_is_not_publication_and_future_clock_is_local_failure():
    item = flow(source_timestamp_seconds=int((STAMP + timedelta(hours=1)).timestamp()))
    value = normalized("eastmoney:etf.trading_flow.snapshot", item)
    assert not value["quality_ok"]
    assert "SOURCE_CLOCK_IN_FUTURE" in value["quality_flags"]
    assert value["records"][0]["publication_at"] is None
    assert value["records"][0]["knowledge_from"] == STAMP.isoformat()


def test_wrong_etf_identity_cannot_reuse_the_unit_contract():
    with pytest.raises(ValueError, match="SOURCE_ETF_SCOPE_MISMATCH"):
        normalized("eastmoney:etf.trading_flow.snapshot", flow(security="sh600519"))


def test_notice_label_and_native_clock_do_not_backdate_knowledge():
    item = Obj(
        security_code="600519",
        announcement_id="AN200101010001",
        title="Synthetic notice",
        notice_date=date(2001, 1, 1),
        display_time_text="2000-12-31 20:01:02:123",
        production_time_text="2000-12-31 20:02:00:000",
    )
    value = normalized("eastmoney:announcement.index", item)
    row = value["records"][0]
    assert row["knowledge_from"] == STAMP.isoformat()
    assert row["source_clock"] is None and row["publication_at"] is None
    assert row["source_display_time_text"].endswith(":123")
    assert value["source_latest_notice_date"] == "2001-01-01"
    assert value["source_latest_notice_age_days"] == 33
    assert "ARCHIVE_ONLY_NOT_CURRENT_DISCLOSURE_COVERAGE" in value["quality_flags"]


def test_next_day_notice_label_is_not_a_future_publication_claim():
    item = Obj(
        security_code="600519",
        announcement_id="AN200102030001",
        title="Synthetic notice",
        notice_date=date(2001, 2, 4),
        display_time_text=None,
        production_time_text=None,
    )
    value = normalized("eastmoney:announcement.index", item)
    assert value["quality_ok"]
    assert value["records"][0]["source_clock"] is None
    assert value["source_latest_notice_age_days"] == -1


def test_convertible_quote_is_per100_face_without_an_extra100_multiplier():
    value = normalized("jisilu:convertible.list", bond())
    row = value["records"][0]
    assert row["metrics"]["price"]["value"] == "111.123"
    assert row["metrics"]["price"]["unit"] == "CNY_PER_100_CNY_FACE"
    assert row["metrics"]["conversion_price"]["unit"] == "CNY_PER_SHARE"
    assert value["quality_ok"]


def test_context_date_and_time_cannot_manufacture_future_or_known_quote_time():
    value = normalized("jisilu:convertible.list", bond())
    row = value["records"][0]
    assert row["source_context_date"] == "2001-02-03"
    assert row["source_last_time_text"] == "15:00:01"
    assert row["source_clock"] is None and row["quote_date"] is None
    assert row["listing_date"] is None and row["listing_state"] == "NOT_INFERRED"
    assert "SOURCE_REFERENCE_QUOTE_CLOCK_UNPROVEN" in value["quality_flags"]
    assert "ANONYMOUS_PARTIAL_LIST_NO_COMPLETENESS" in value["quality_flags"]


def test_exchangeable_metadata_keeps_values_outside_convertible_unit_admission():
    value = normalized("jisilu:convertible.list", bond(bond_type="E", bond_code="132001"))
    row = value["records"][0]
    assert row["metrics"] == {}
    assert row["unqualified_source_values"]["price"] == "111.123"
    assert value["unit_contract"] == "VERIFIED_CONVERTIBLE_FIELDS_EXCHANGEABLE_METADATA_ONLY"
    assert row["unit_admission"] == "METADATA_ONLY_UNQUALIFIED_EXCHANGEABLE"


def test_new_tranche_never_requests_other_registered_sources_implicitly():
    pytest.importorskip("astock")
    version = importlib.metadata.version("astock-sources")
    base = policy(
        CONFIG if version == "0.1.2" else ROOT / "config/pipelines/p10-supplement-sources.v1.json"
    )
    subset = copy.deepcopy(base)
    subset["sources"] = subset["sources"][:2]
    subset["capability_selection"] = "EXACT_DECLARED_SUBSET"
    assert set(library_contracts(subset)) == {x["key"] for x in subset["sources"]}
    del subset["capability_selection"]
    with pytest.raises(ValueError, match="SOURCE_CAPABILITY_SET_MISMATCH"):
        library_contracts(subset)
    subset["capability_selection"] = "EXACT_DECLARED_SUBSET"
    subset["sources"][0]["key"] = "unknown:capability"
    with pytest.raises(ValueError, match="SOURCE_CAPABILITY_SET_MISMATCH"):
        library_contracts(subset)
