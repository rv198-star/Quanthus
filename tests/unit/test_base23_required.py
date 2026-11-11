from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_required import (
    CONTRACT_ID,
    qualify_daily_observation,
    revise_share_units,
)

from .test_base22_canonical_action_revision import case


def fixture():
    raw, old, donor, cash, _, clock, _ = case()
    raw.update(source="md.dividend_history", bonus_shares=1.23456789)
    term = {
        **cash[0],
        "term_type": "BONUS_SHARES_PER_SHARE",
        "amount_per_share": None,
        "currency": None,
        "ratio_numerator": Decimal("1.2345678900"),
        "ratio_denominator": Decimal(1),
        **{
            k: old[k]
            for k in (
                "corporate_action_id",
                "knowledge_from",
                "source_revision",
                "source_artifact_sha256",
                "record_source",
                "source_record_id",
                "announced_at",
            )
        },
    }
    dt = {
        **term,
        **{
            k: donor[k]
            for k in (
                "corporate_action_id",
                "knowledge_from",
                "source_revision",
                "source_artifact_sha256",
                "record_source",
                "source_record_id",
                "announced_at",
            )
        },
        "ratio_numerator": Decimal("0.12345678900"),
    }
    vocabulary = {"share_quantity_unit": "per_ten_shares"}
    binding = {
        "purpose": CONTRACT_ID,
        "original_raw_row_sha256": digest(raw),
        "before_row_sha256": digest(old),
        "before_term_rows_sha256": [digest(term)],
        "donor_row_sha256": digest(donor),
        "donor_term_rows_sha256": [digest(dt)],
        "vocabulary_identity": digest(vocabulary),
        "original_member_sha256": old["source_artifact_sha256"],
        **{
            k: "sha256:" + "a" * 64
            for k in (
                "original_manifest_sha256",
                "donor_scope_identity",
                "donor_raw_replay_identity",
                "vocabulary_sha256",
            )
        },
    }
    return [raw, old, [term], donor, [dt], vocabulary, binding, clock]


def project(c):
    return revise_share_units(*c[:5], vocabulary=c[5], binding=c[6], observed_at=c[7])


def test_explicit_unit_successor_keeps_identity_and_old_atoms_and_real_time():
    c = fixture()
    frozen = deepcopy(c)
    out, proof = project(c)
    assert c == frozen
    assert out["dwd_corporate_action"][0]["knowledge_to"] == c[7]
    assert out["dwd_corporate_action"][1]["corporate_action_id"] == c[1]["corporate_action_id"]
    assert (
        out["dwd_corporate_action_term"][0]["ratio_numerator"].as_tuple()
        == c[2][0]["ratio_numerator"].as_tuple()
    )
    assert out["dwd_corporate_action_term"][1]["ratio_numerator"] == Decimal("0.123456789")
    assert out["dwd_corporate_action"][1]["announced_at"] == c[1]["announced_at"]
    assert not proof["source_priority_granted"]
    assert not proof["donor_event_id_adopted"]
    assert not proof["historical_PIT_admission"]


def test_unrepresentable_eleventh_share_decimal_fails_without_rounding():
    c = fixture()
    c[0]["bonus_shares"] = 1.2345678901
    c[2][0]["ratio_numerator"] = Decimal("1.2345678901")
    c[4][0]["ratio_numerator"] = Decimal("0.12345678901")
    c[6].update(
        original_raw_row_sha256=digest(c[0]),
        before_term_rows_sha256=[digest(t) for t in c[2]],
        donor_term_rows_sha256=[digest(t) for t in c[4]],
    )
    with pytest.raises(BoundedError, match="SCHEMA_CANNOT_PRESERVE_EXACT_VALUE"):
        project(c)


def test_cash_conflict_is_not_a_share_unit_repair():
    c = fixture()
    _, old, donor, cash, _, _, _ = case()
    before_cash = {
        **cash[0],
        **{
            k: old[k]
            for k in (
                "corporate_action_id",
                "knowledge_from",
                "record_source",
                "source_record_id",
                "source_revision",
                "source_artifact_sha256",
                "announced_at",
            )
        },
    }
    donor_cash = {**cash[0], "amount_per_share": Decimal("9.0")}
    c[2].append(before_cash)
    c[4].append(donor_cash)
    c[6].update(
        before_term_rows_sha256=sorted(digest(t) for t in c[2]),
        donor_term_rows_sha256=sorted(digest(t) for t in c[4]),
    )
    with pytest.raises(BoundedError, match="OTHER_EFFECT_CONFLICT"):
        project(c)


@pytest.mark.parametrize(
    "damage",
    [
        "unit",
        "raw",
        "member",
        "parent",
        "date",
        "number",
        "binding",
        "term_version",
        "clock",
        "donor_conflict",
    ],
)
def test_unit_similarity_never_substitutes_for_complete_binding(damage):
    c = fixture()
    if damage == "unit":
        c[5]["share_quantity_unit"] = "per_share"
    elif damage == "raw":
        c[0]["bonus_shares"] = 2
    elif damage == "member":
        c[6]["original_member_sha256"] = "sha256:" + "b" * 64
    elif damage == "parent":
        c[3]["security_id"] = "other"
    elif damage == "date":
        c[0]["ex_date"] = date(2026, 10, 1)
    elif damage == "number":
        c[4][0]["ratio_numerator"] = Decimal("NaN")
    elif damage == "binding":
        c[6]["donor_scope_identity"] = "sha256:" + "z" * 64
    elif damage == "term_version":
        c[4][0]["knowledge_from"] = c[7]
    elif damage == "clock":
        c[7] = c[1]["knowledge_from"]
    else:
        c[4][0]["ratio_numerator"] = Decimal("0.333")
    # Rebind typed hashes: semantic violations must reject even after repacking.
    c[6].update(
        original_raw_row_sha256=digest(c[0]),
        before_row_sha256=digest(c[1]),
        donor_row_sha256=digest(c[3]),
        donor_term_rows_sha256=[digest(t) for t in c[4]],
        vocabulary_identity=digest(c[5]),
    )
    with pytest.raises(BoundedError):
        project(c)


def daily_fixture():
    t = datetime(2026, 8, 3, tzinfo=timezone.utc)
    old = datetime(2026, 7, 31, tzinfo=timezone.utc)
    row = {
        "listing_id": "L",
        "calendar_session_id": "C",
        "observation_start": t,
        "knowledge_from": t,
        "is_tradable": True,
    }
    listing = {
        "listing_id": "L",
        "security_id": "S",
        "venue_id": "V",
        "effective_from": old,
        "effective_to": None,
        "knowledge_from": old,
        "knowledge_to": None,
    }
    cal = {
        "calendar_session_id": "C",
        "venue_id": "V",
        "trade_date": date(2026, 8, 3),
        "is_open": True,
    }
    interval = {
        "listing_id": "L",
        "effective_from": old,
        "effective_to": None,
        "knowledge_from": old,
        "knowledge_to": None,
        "status_type": "SUSPENSION",
        "status_value": "SUSPENDED",
    }
    return row, listing, cal, interval, t


def test_dated_observation_conflict_blocks_entry_without_forced_sale_or_interval_rewrite():
    row, listing, cal, interval, t = daily_fixture()
    frozen = deepcopy((row, interval))
    result = qualify_daily_observation(row, listing, cal, [interval], observed_at=t)
    assert result["disposition"] == "CONFLICT_BLOCKS_NEW_ENTRY"
    assert not result["force_sale"]
    assert (row, interval) == frozen
    interval["knowledge_from"] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert (
        qualify_daily_observation(row, listing, cal, [interval], observed_at=t)["disposition"]
        == "DATED_PROVIDER_OBSERVATION_CONSISTENT"
    )


def test_daily_business_day_uses_shanghai_date_without_changing_instant():
    row, listing, cal, _, t = daily_fixture()
    instant = datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
    row["observation_start"] = instant
    result = qualify_daily_observation(row, listing, cal, [], observed_at=t)
    assert result["trade_date"] == "2026-08-03"
    assert row["observation_start"] == instant


@pytest.mark.parametrize(
    "damage", ["date", "venue", "closed", "listing_clock", "future", "boolean"]
)
def test_daily_date_venue_knowledge_and_exact_bool_fail_closed(damage):
    row, listing, cal, interval, t = daily_fixture()
    if damage == "date":
        cal["trade_date"] = date(2026, 8, 4)
    elif damage == "venue":
        cal["venue_id"] = "OTHER"
    elif damage == "closed":
        cal["is_open"] = False
    elif damage == "listing_clock":
        listing["knowledge_from"] = datetime(2026, 9, 1, tzinfo=timezone.utc)
    elif damage == "future":
        t = datetime(2026, 8, 2, tzinfo=timezone.utc)
    else:
        row["is_tradable"] = 1
    with pytest.raises(BoundedError):
        qualify_daily_observation(row, listing, cal, [interval], observed_at=t)
