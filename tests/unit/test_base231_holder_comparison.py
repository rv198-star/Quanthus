from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_holder_comparison import compare_matrices


def matrix():
    return [
        dict(
            security_id="s",
            company_id="c",
            holding_type="TOP_SHAREHOLDERS",
            observation_start=datetime(2026, 9, 29, 16, tzinfo=timezone.utc),
            holding_quantity_unit="SHARE",
            holding_percentage_unit="PERCENT",
            holder_rank=n,
            holder_match_key="holder" + str(n),
            holding_quantity=Decimal(1000 + n),
            holding_percentage=Decimal("1.12"),
        )
        for n in range(1, 11)
    ]


def test_exact_quantities_do_not_require_erasing_original_percentage_precision():
    new, old = matrix(), matrix()
    old[0]["holding_percentage"] = Decimal("1.123456789")
    before = deepcopy(old)
    result = compare_matrices(new, old)
    assert result["same_members_and_quantities"]
    assert result["classifications"]["PERCENTAGE_DIFFERENCE"] == 1
    assert not result["historical_provider_conflict_arbitrated"]
    assert old == before


def test_unknown_percentage_is_not_zero_or_an_agreement():
    new, old = matrix(), matrix()
    new[0]["holding_percentage"] = None
    result = compare_matrices(new, old)
    assert result["classifications"]["PERCENTAGE_NOT_COMPARABLE"] == 1
    assert not result["classifications"].get("PERCENTAGE_DIFFERENCE")


def test_quantity_difference_or_absent_member_is_not_netted_or_exited():
    new, old = matrix(), matrix()
    old[0]["holding_quantity"] += 1
    result = compare_matrices(new, old)
    assert not result["same_members_and_quantities"]
    assert result["classifications"]["QUANTITY_DIFFERENCE"] == 1
    old[0]["holder_match_key"] = "other"
    result = compare_matrices(new, old)
    assert result["classifications"]["PREVIOUS_ONLY_MEMBER_NOT_EXIT"] == 1
    assert not result["missing_member_is_exit"]


@pytest.mark.parametrize("fault", ["date", "company", "kind", "ranks", "unit", "duplicate"])
def test_incompatible_or_incomplete_matrices_fail_closed(fault):
    new, old = matrix(), matrix()
    if fault == "date":
        old[0]["observation_start"] = datetime(2026, 9, 28, tzinfo=timezone.utc)
    elif fault == "company":
        old[0]["company_id"] = "different"
    elif fault == "kind":
        old[0]["holding_type"] = "TOP_FLOATING_SHAREHOLDERS"
    elif fault == "ranks":
        new[0]["holder_rank"] = 2
    elif fault == "unit":
        old[0]["holding_quantity_unit"] = "TEN_THOUSAND_SHARES"
    else:
        old[0]["holder_match_key"] = old[1]["holder_match_key"]
    with pytest.raises(RuntimeError):
        compare_matrices(new, old)


def test_percentage_of_a_share_class_is_not_a_total_share_percentage():
    new, old = matrix(), matrix()
    old[0]["holding_percentage_unit"] = "PERCENT_OF_LITERAL_A_SHARES"
    assert compare_matrices(new, old)["classifications"]["PERCENTAGE_NOT_COMPARABLE"] == 1


def test_explicit_share_word_alias_does_not_change_quantity_or_original_rows():
    new, old = matrix(), matrix()
    old[0]["holding_quantity_unit"] = "股"
    assert compare_matrices(new, old)["same_members_and_quantities"]
    assert old[0]["holding_quantity_unit"] == "股"


@pytest.mark.parametrize("suffix,token", [("万股", "0.1001万"), ("亿股", "0.00001001亿股")])
def test_source_literal_unit_is_not_applied_twice_to_normalized_native_quantity(suffix, token):
    new, old = matrix(), matrix()
    old[0].update(holding_quantity_unit=suffix, raw_holding_quantity=token)
    assert compare_matrices(new, old)["same_members_and_quantities"]
    assert old[0]["holding_quantity"] == Decimal(1001)
    old[0]["raw_holding_quantity"] = token.replace("1001", "1002")
    with pytest.raises(RuntimeError):
        compare_matrices(new, old)
