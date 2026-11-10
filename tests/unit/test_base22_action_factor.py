import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22_action_factor import (
    normalize_retained_factor_record,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError


def inputs():
    record = {
        "stock_code": "000001",
        "source_row_key": "exact retained raw row",
        "ex_date": date(2020, 6, 8),
        "announce_date": date(2020, 6, 1),
        "progress": "实施",
        "cash_per_share": 1.0,
        "bonus_shares": 0.0,
        "transfer_shares": 0.0,
    }
    policy = json.loads(Path("config/p1-corporate-action-normalization.v3.json").read_bytes())
    return record, {
        "prior_close": Decimal(10),
        "factor_before": Decimal(1),
        "factor_after": Decimal(10) / Decimal("9.9"),
        "baseline": "prior_factor",
        "policy": policy,
    }


def test_retrospective_numbers_use_frozen_units_without_supplying_historical_known_at():
    record, kwargs = inputs()
    before = deepcopy((record, kwargs))
    out, proof = normalize_retained_factor_record(record, **kwargs)
    assert (record, kwargs) == before
    assert out["cash_per_share"] == Decimal("0.1")
    assert out["bonus_shares"] == out["transfer_shares"] == 0
    assert proof["provider_acquisition"] is proof["historical_publication_instant"] is None
    assert not proof["legacy_known_at_or_accepted_child_inferred"]
    assert not proof["original_raw_atoms_or_prices_changed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_per_share", None),
        ("bonus_shares", None),
        ("transfer_shares", None),
        ("cash_per_share", float("nan")),
        ("cash_per_share", True),
        ("ex_date", date(2026, 9, 1)),
    ],
)
def test_unknown_terms_are_not_zero_or_guessed(field, value):
    record, kwargs = inputs()
    record[field] = value
    with pytest.raises(BoundedError):
        normalize_retained_factor_record(record, **kwargs)


@pytest.mark.parametrize("mutation", ["policy", "factor", "baseline", "prior"])
def test_changed_policy_or_invalid_crosscheck_is_rejected(mutation):
    record, kwargs = inputs()
    if mutation == "policy":
        kwargs["policy"]["maximum_factor_ratio_error"] = "0.5"
    elif mutation == "factor":
        kwargs["factor_after"] = Decimal(2)
    elif mutation == "baseline":
        kwargs["baseline"] = "guessed"
    else:
        kwargs["prior_close"] = 0
    with pytest.raises(BoundedError):
        normalize_retained_factor_record(record, **kwargs)
