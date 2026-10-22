from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_column_continuation import (
    holder_observation,
)
from tests.unit.test_base22_f10_ownership import raw, resolve

BODY = (
    "截至日期：2026-06-30 十大股东情况\n"
    "｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜\n"
    "｜合成股东｜1.234567万｜1.123456789012%｜境内非国有法人｜不变｜\n"
    "｜｜｜｜股｜｜\n└"
)


def project(body=BODY):
    return holder_observation(
        raw(body, "股东研究"), source_sha256="sha256:" + "a" * 64, resolver=resolve()
    )


def test_column_aligned_classification_wrap_preserves_scalars_name_and_real_clock():
    rows, proof = project()
    row = rows["dwd_shareholding_observation"][0]
    assert row["share_nature"] == "境内非国有法人股"
    assert row["shareholder_source_name"] == "合成股东"
    assert row["holding_quantity"] == Decimal("12345.67")
    assert row["raw_holding_quantity"] == "1.234567万"
    assert row["holding_percentage"] == Decimal("1.123456789012")
    assert proof["column_continuation_rows"] == 1
    assert proof["numeric_token_sequence_preserved"] is True
    assert proof["unqualified_increment_comments"]["nonempty_cells"] == 1
    assert proof["unqualified_increment_comments"]["qualification"] == "NOT_GRANTED"


def test_name_and_classification_fold_only_into_the_same_previous_numeric_row():
    rows, _ = project(BODY.replace("｜｜｜｜股｜｜", "｜续名｜｜｜股｜｜"))
    row = rows["dwd_shareholding_observation"][0]
    assert row["shareholder_source_name"] == "合成股东续名"
    assert row["share_nature"] == "境内非国有法人股"


def test_floating_header_keeps_the_classification_in_its_existing_model_field():
    rows, _ = project(BODY.replace("十大股东情况", "十大流通股东情况"))
    row = rows["dwd_shareholding_observation"][0]
    assert row["holder_category"] == "境内非国有法人股"
    assert row["share_nature"] is None


@pytest.mark.parametrize(
    "bad", ["｜｜2万｜｜股｜｜", "｜｜｜1%｜股｜｜", "｜｜｜｜1股｜｜", "｜｜｜｜股｜增持1万｜"]
)
def test_numeric_or_other_column_cannot_be_hidden_as_classification_wrap(bad):
    with pytest.raises(BoundedError):
        project(BODY.replace("｜｜｜｜股｜｜", bad))


def test_orphan_and_unknown_header_continuations_fail_closed():
    for body in [
        BODY.replace("｜合成股东｜", "｜｜｜｜股｜｜\n｜合成股东｜"),
        BODY.replace("股份性质", "未定义性质"),
    ]:
        with pytest.raises(BoundedError):
            project(body)


def test_source_hash_and_clock_remain_original_required_evidence():
    for defect in ["content_sha256", "acquired_at"]:
        r = raw(BODY, "股东研究")
        r[defect] = None
        with pytest.raises(BoundedError):
            holder_observation(r, source_sha256="sha256:" + "a" * 64, resolver=resolve())


def test_frozen_literal_profile_stays_fail_closed_for_this_shape():
    from quanthus.pipelines.securities_warehouse.base22_holder_literals import (
        holder_observation as old,
    )

    with pytest.raises(BoundedError):
        old(raw(BODY, "股东研究"), source_sha256="sha256:" + "a" * 64, resolver=resolve())
