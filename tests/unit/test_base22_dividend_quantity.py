"""Correct an evidenced unit error without selecting a competing cash source."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_dividend_quantity import (
    correct_dividend_action_type,
    correct_dividend_quantity,
    validate_quantity_versions,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import _stable_id


def fixture():
    notice = datetime(2020, 5, 1)
    ex = datetime(2020, 6, 1)
    clock = datetime(2020, 6, 1, 15, 59, 59, 999999, tzinfo=timezone.utc)
    raw = {
        "stock_code": "000001",
        "announce_date": notice,
        "ex_dividend_date": ex,
        "dividend_per_share": 0.2,
        "bonus_shares": 0.1234567891,
        "transfer_shares": 0.0,
        "progress": "实施",
    }
    key = _stable_id(
        "action_source", "000001", "2020-06-01", str(notice), "0.2", "0.1234567891", ""
    )
    action_id = _stable_id("corporate_action", "dividend_history", key)
    common = {
        "corporate_action_id": action_id,
        "source_record_id": key,
        "record_source": "dividend_history",
        "source_revision": "old-native",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "knowledge_from": clock,
        "knowledge_to": None,
        "effective_at": clock,
        "announced_at": clock,
    }
    action = {
        **common,
        "action_type": "CASH_DIVIDEND",
        "action_status": "IMPLEMENTED",
        "ex_date": ex.date(),
    }
    term = {
        **common,
        "corporate_action_term_id": _stable_id(
            "corporate_action_term", action_id, "BONUS_SHARES_PER_SHARE"
        ),
        "term_type": "BONUS_SHARES_PER_SHARE",
        "ratio_numerator": Decimal("0.1234567891"),
        "ratio_denominator": Decimal("1.0000000000"),
        "amount_per_share": None,
        "option_id": None,
        "currency": None,
        "subscription_price": None,
    }
    return raw, action, term


def run(raw, action, term):
    return correct_dividend_quantity(
        raw,
        action,
        term,
        raw_sha256="sha256:" + "a" * 64,
        observed_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        revision="p10-base22-native-dividend-perten-ratio-revision-v1:sha256:" + "b" * 64,
    )


def test_eleven_decimal_ratio_preserves_original_numerator_and_new_real_knowledge():
    raw, action, term = fixture()
    before = deepcopy((raw, action, term))
    new, proof = run(raw, action, term)
    assert (raw, action, term) == before
    assert new["ratio_numerator"] == term["ratio_numerator"]
    assert new["ratio_denominator"] == Decimal("10.0000000000")
    assert new["ratio_numerator"] / new["ratio_denominator"] == Decimal("0.01234567891")
    assert new["knowledge_from"].year == 2026 and new["knowledge_from"].month == 10
    assert new["announced_at"] is None
    assert proof["provider_acquisition"] is None
    assert not proof["old_normalization_recovered"] and not proof["cash_source_priority_granted"]


@pytest.mark.parametrize(
    "mutation",
    [
        "code",
        "raw_value",
        "raw_null",
        "raw_nan",
        "before_number",
        "before_denominator",
        "before_source",
        "before_hash",
        "before_id",
        "cash",
        "closed",
        "zero",
        "clock",
    ],
)
def test_only_frozen_nonzero_share_quantity_keys_can_be_repaired(mutation):
    raw, action, term = fixture()
    if mutation == "code":
        raw["stock_code"] = "000002"
    elif mutation == "raw_value":
        raw["bonus_shares"] = 1.0
    elif mutation == "raw_null":
        raw["bonus_shares"] = None
    elif mutation == "raw_nan":
        raw["bonus_shares"] = float("nan")
    elif mutation == "before_number":
        term["ratio_numerator"] = Decimal("0.12")
    elif mutation == "before_denominator":
        term["ratio_denominator"] = Decimal(10)
    elif mutation == "before_source":
        term["record_source"] = "other"
    elif mutation == "before_hash":
        term["source_artifact_sha256"] = "sha256:" + "b" * 64
    elif mutation == "before_id":
        term["corporate_action_term_id"] = "wrong"
    elif mutation == "cash":
        term["term_type"] = "CASH_DIVIDEND_PER_SHARE"
    elif mutation == "closed":
        term["knowledge_to"] = datetime(2026, 1, 1, tzinfo=timezone.utc)
    elif mutation == "zero":
        raw["bonus_shares"] = 0.0
    elif mutation == "clock":
        term["knowledge_from"] = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(BoundedError):
        run(raw, action, term)


@pytest.mark.parametrize(
    "mutation", [None, "old-number", "new-number", "zero", "missing", "duplicate"]
)
def test_partition_conservation_preserves_every_old_atom_and_only_closes_selected_interval(
    mutation,
):
    raw, action, before = fixture()
    new, _ = run(raw, action, before)
    key = tuple(
        before[k] for k in ("corporate_action_id", "corporate_action_term_id", "effective_at")
    )
    zero = dict(before, corporate_action_term_id="unselected-zero", ratio_numerator=Decimal(0))
    after = [{**before, "knowledge_to": new["knowledge_from"]}, zero.copy(), new.copy()]
    if mutation == "old-number":
        after[0]["ratio_numerator"] = Decimal(123)
    elif mutation == "new-number":
        after[2]["ratio_denominator"] = Decimal(1)
    elif mutation == "zero":
        after[1]["ratio_numerator"] = Decimal(1)
    elif mutation == "missing":
        after.pop()
    elif mutation == "duplicate":
        after.append(new.copy())
    if mutation is None:
        proof = validate_quantity_versions([before, zero], after, {key: new})
        assert proof["before_rows"] == 2 and proof["after_rows"] == 3
        assert proof["exact_new_versions"] == 1
    else:
        with pytest.raises(BoundedError):
            validate_quantity_versions([before, zero], after, {key: new})


def test_parent_action_is_versioned_without_reinterpreting_status_or_cash():
    raw, before, _ = fixture()
    old = deepcopy(before)
    new = correct_dividend_action_type(
        raw,
        before,
        raw_sha256="sha256:" + "a" * 64,
        observed_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        revision="p10-base22-native-dividend-perten-ratio-revision-v1:sha256:" + "b" * 64,
    )
    assert before == old
    assert new["action_type"] == "MIXED_DISTRIBUTION"
    assert new["action_status"] == before["action_status"]
    assert new["ex_date"] == before["ex_date"] and new["announced_at"] is None
    key = (before["corporate_action_id"], before["effective_at"])
    result = validate_quantity_versions(
        [before],
        [{**before, "knowledge_to": new["knowledge_from"]}, new],
        {key: new},
        action=True,
    )
    assert result["exact_new_versions"] == 1


@pytest.mark.parametrize("mutation", ["status", "raw-null", "effective", "hash"])
def test_parent_requires_original_status_dates_and_complete_raw_binding(mutation):
    raw, before, _ = fixture()
    if mutation == "status":
        raw["progress"] = "预案"
    elif mutation == "raw-null":
        raw["dividend_per_share"] = None
    elif mutation == "effective":
        before["effective_at"] = datetime(2020, 6, 2, tzinfo=timezone.utc)
    else:
        before["source_artifact_sha256"] = "sha256:" + "c" * 64
    with pytest.raises(BoundedError):
        correct_dividend_action_type(
            raw,
            before,
            raw_sha256="sha256:" + "a" * 64,
            observed_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
            revision="p10-base22-native-dividend-perten-ratio-revision-v1:sha256:" + "b" * 64,
        )
