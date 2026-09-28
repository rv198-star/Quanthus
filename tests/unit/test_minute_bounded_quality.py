"""Owner's bounded precision does not license missing data or modified bars."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.providers.minute_quality_policy import BOUNDED, STRICT, load_policy
from quanthus.providers.minute_source_selection import choose_whole_security, qualify_candidate
from quanthus.providers.paid_minute_source import normalized_candidate, qualify_raw
from tests.unit.test_minute_source_selection import DATE, candidate
from tests.unit.test_paid_minute_source import DATE as PAID_DATE
from tests.unit.test_paid_minute_source import FIELDS, payload, start_labeled_payload


def complete(provider="tencent"):
    item = copy.deepcopy(candidate(provider))
    for row in item.rows:
        row["amount"] = 11.3
    return item


def qualify(item, policy=BOUNDED, **kwargs):
    return qualify_candidate(
        item, symbol="000001", market_date=DATE, daily_volume=240,
        daily_close=11.3, traded=True, quality_policy=policy, **kwargs,
    )


def choose(*items):
    return choose_whole_security(
        items, symbol="000001", market_date=DATE, daily_volume=240,
        daily_close=11.3, traded=True, priority=("tencent", "relay", "third"),
        quality_policy=BOUNDED,
    )


def test_versioned_policy_is_explicit_and_cannot_be_silently_widened(tmp_path):
    path = Path(__file__).resolve().parents[2] / "config/minute-quality/bounded-v1.json"
    assert load_policy(path) == BOUNDED
    assert load_policy(None) == STRICT
    value = json.loads(path.read_bytes())
    value["price_abs_cny"] = "0.02"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unsupported"):
        load_policy(bad)
    bad.write_text('{"id":1,"id":1}')
    with pytest.raises(ValueError, match="duplicate"):
        load_policy(bad)


@pytest.mark.parametrize("delta,passes", [
    ("0", True), ("0.01", True), ("-0.01", True),
    ("0.010000000000002", True), ("0.010000001", False), ("0.02", False),
])
def test_cent_boundary_without_rounding_candidate(delta, passes):
    left, right = complete(), complete("relay")
    right.rows[0]["high"] = Decimal("11.3") + abs(Decimal(delta))
    before = copy.deepcopy(right)
    result = choose(left, right)
    assert (result.status == "qualified_whole_security_candidate") is passes
    assert right == before
    if passes:
        assert result.selected_provider == "tencent"
        assert result.normalized_sha256 == qualify(left)
        assert result.quality_policy_id == BOUNDED.id
    if Decimal(delta) and passes:
        assert result.tolerated_differences


def test_comparison_is_pairwise_not_transitive():
    a, b, c = complete(), complete("relay"), complete("third")
    b.rows[0]["high"] = Decimal("11.31")
    c.rows[0]["high"] = Decimal("11.32")
    assert choose(a, b, c).status == "blocked_cross_source_conflict"


def test_day_totals_do_not_license_minute_share_redistribution():
    a, b = complete(), complete("relay")
    b.rows[0]["volume"] = 2
    b.rows[1]["volume"] = 0
    assert choose(a, b).status == "blocked_cross_source_conflict"


@pytest.mark.parametrize("daily,delta,passes", [
    ("104381872", "28", True), ("10000000", "100", True),
    ("10000000", "101", False), ("1000000", "10", True),
    ("1000000", "11", False), ("0", "1", False),
])
def test_volume_requires_both_absolute_and_relative_limits(daily, delta, passes):
    reference = Decimal(daily)
    assert BOUNDED.volume_matches(reference + Decimal(delta), reference) is passes


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume", "amount"])
@pytest.mark.parametrize("bad", [None, True, "NaN", "Infinity"])
def test_null_boolean_and_nonfinite_required_fields_are_never_precision_noise(field, bad):
    item = complete()
    item.rows[10][field] = bad
    with pytest.raises(ValueError):
        qualify(item)


@pytest.mark.parametrize("mode", ["missing", "wrong_date", "duplicate", "wrong_code"])
def test_complete_correct_identity_grid_is_still_mandatory(mode):
    item = complete()
    if mode == "missing":
        item = replace(item, rows=item.rows[:-1])
    elif mode == "wrong_date":
        item.rows[0]["ts"] = "2026-09-23 09:31:00"
    elif mode == "duplicate":
        item.rows[1]["ts"] = item.rows[0]["ts"]
    else:
        item.rows[0]["stock_code"] = "000002"
    with pytest.raises(ValueError):
        qualify(item)


def test_source_reported_zero_is_not_a_missing_minute():
    item = complete()
    item.rows[0]["volume"] = 0
    item.rows[0]["amount"] = 0
    item.rows[1]["volume"] = 2
    assert qualify(item)


def test_strict_default_still_rejects_cent_differences_and_allows_known_amount_debt():
    left, right = candidate(), candidate("relay")
    right.rows[0]["high"] = 11.31
    result = choose_whole_security(
        [left, right], symbol="000001", market_date=DATE, daily_volume=240,
        daily_close=11.3, traded=True, priority=("tencent", "relay"),
    )
    assert result.status == "blocked_cross_source_conflict"
    assert result.quality_policy_id == STRICT.id
    assert qualify_candidate(
        left, symbol="000001", market_date=DATE, daily_volume=240,
        daily_close=11.3, traded=True,
    )
    with pytest.raises(ValueError, match="amount"):
        qualify(left)


@pytest.mark.parametrize("mode", ["end", "start"])
def test_paid_replay_keeps_actual_amount_and_does_not_infer_it(mode):
    value = payload() if mode == "end" else start_labeled_payload()
    raw = json.dumps(value).encode()
    arguments = dict(
        symbol="000001.SZ", market_date=PAID_DATE,
        daily_volume=2500 if mode == "end" else 2400, daily_close=11,
        first_5m_volume=150 if mode == "end" else 50,
        raw_ref="captured.json.gz", quality_policy=BOUNDED,
    )
    item, result = qualify_raw(raw, **arguments)
    assert item == normalized_candidate(raw, **arguments)
    assert item.rows[0]["amount"] == (1210 if mode == "end" else 110)
    assert sum(row["amount"] for row in item.rows) == (27500 if mode == "end" else 26400)
    assert result["quality_policy"]["id"] == BOUNDED.id
    # Keep the original bytes untouched; old exact behavior remains unchanged.
    assert json.loads(raw) == value
    assert all(row["amount"] is None for row in normalized_candidate(
        raw, **{key: val for key, val in arguments.items() if key != "quality_policy"}
    ).rows)


def test_paid_auction_or_minute_missing_amount_is_rejected_not_estimated():
    for stamp in ("09:30:00", "10:01:00"):
        value = payload()
        row = next(row for row in value["data"]["items"] if row[1].endswith(stamp))
        row[FIELDS.index("amount")] = None
        with pytest.raises(ValueError, match="missing minute amount"):
            qualify_raw(
                json.dumps(value).encode(), symbol="000001.SZ", market_date=PAID_DATE,
                daily_volume=2500, daily_close=11, first_5m_volume=150,
                raw_ref="captured.json.gz", quality_policy=BOUNDED,
            )


def test_amount_bytes_are_bound_to_candidate_hash_and_not_silently_ignored():
    left, right = complete(), complete("relay")
    right.rows[0]["amount"] = 11.31
    assert qualify(left) != qualify(right)
    assert choose(left, right).status == "blocked_cross_source_conflict"


def test_undeclared_programmatic_policy_cannot_bypass_approved_bounds():
    with pytest.raises(ValueError, match="unsupported"):
        qualify(complete(), replace(BOUNDED, price_abs=Decimal("1")))


def test_offline_bounded_replay_retains_old_receipts_and_rejects_tencent_missing_amount(tmp_path):
    from tests.unit.test_minute_offline_ledger import fixture, put_paid, put_tencent, run

    refs, tencent, paid = fixture(tmp_path)
    put_tencent(tencent)
    _, receipt = put_paid(paid, old_label="unqualified")
    before = receipt.read_bytes()
    summary, rows, output = run(tmp_path, refs, tencent, paid, quality_policy=BOUNDED)
    assert summary["processed"] == 1
    assert rows[0]["selected_provider"] == "paid-tushare-style-proxy-stk_mins"
    assert "MISSING_MINUTE_AMOUNT" in rows[0]["sources"][0]["reason_codes"]
    assert receipt.read_bytes() == before
    assert summary["quality_policy"]["semantic_sha256"] == BOUNDED.evidence()["semantic_sha256"]
    assert not summary["publication_authorized"]
    assert not list(output.rglob("*.parquet"))
